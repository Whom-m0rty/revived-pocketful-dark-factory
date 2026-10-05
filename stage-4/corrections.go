package main

import (
	"net/http"
	"time"
	"unicode/utf8"
)

// correction is one validated, not yet applied, change to a payment's amount history.
type correction struct {
	payment       *Payment
	expected      int64
	amount        int64
	effectiveText string
	effective     time.Time
	reason        string
}

// parseCorrection validates the ordinary correction fields; every invalid input is 422.
func parseCorrection(body map[string]any, now time.Time) (correction, *apiError) {
	var c correction
	var ok bool
	if c.expected, ok = integralValue(body["expected_revision"]); !ok || c.expected < 1 {
		return c, errValidation("expected_revision must be a positive integer")
	}
	if c.amount, ok = integralValue(body["amount"]); !ok || c.amount < 0 || c.amount > maxAmount {
		return c, errValidation("amount must be an integer from 0 to 1000000000")
	}
	c.effectiveText, _ = body["effective_at"].(string)
	if c.effective, ok = parseInstant(c.effectiveText); !ok || c.effective.After(now) {
		return c, errValidation("effective_at must be an RFC 3339 instant that is not in the future")
	}
	c.reason, ok = body["reason"].(string)
	if n := utf8.RuneCountInString(c.reason); !ok || n < 1 || n > maxNoteLength {
		return c, errValidation("reason must be 1 to 200 characters")
	}
	return c, nil
}

func errLinkedImmutable() *apiError {
	return &apiError{422, "linked_payment_immutable", "this payment is linked and cannot be corrected"}
}

func errRefundExceeds() *apiError {
	return &apiError{422, "refund_exceeds_payment", "refunds would exceed the payment's corrected amount"}
}

// refundedAmount is the total already refunded from the payment.
func (s *State) refundedAmount(p *Payment) int64 {
	var refunded int64
	for _, other := range s.Payments {
		if other.RefundOf != nil && *other.RefundOf == p.ID {
			refunded += other.Amount
		}
	}
	return refunded
}

// checkRevisionAndRefunds checks a correction against the payment's current history.
func (s *State) checkRevisionAndRefunds(c correction) *apiError {
	if c.expected != c.payment.latestRevision().Revision {
		return &apiError{409, "stale_revision", "the payment has been corrected since that revision"}
	}
	if c.amount < s.refundedAmount(c.payment) {
		return errRefundExceeds()
	}
	return nil
}

// applyCorrections records the corrections together, sharing one recorded_at, if the
// combined result is affordable now and never overdraws a wallet in the past. On failure
// nothing changes.
func (s *State) applyCorrections(corrections []correction, batchID *string, now time.Time) ([]*Revision, *apiError) {
	net := map[*User]int64{}
	recorded := now
	for _, c := range corrections {
		change := c.amount - c.payment.latestRevision().Amount
		net[s.usersByID[c.payment.FromUserID]] -= change
		net[s.usersByID[c.payment.ToUserID]] += change
		if last := c.payment.latestRevision().recorded; !recorded.After(last) {
			recorded = last.Add(time.Millisecond)
		}
	}
	for u, delta := range net {
		if delta < 0 && s.available(u, now)+delta < 0 {
			return nil, errInsufficientFunds()
		}
	}

	revisions := make([]*Revision, len(corrections))
	for i, c := range corrections {
		revisions[i] = &Revision{Revision: c.payment.latestRevision().Revision + 1, Amount: c.amount,
			EffectiveAt: c.effectiveText, RecordedAt: formatTime(recorded), Reason: c.reason,
			CorrectionBatchID: batchID, effective: c.effective, recorded: recorded}
		c.payment.Revisions = append(c.payment.Revisions, revisions[i])
	}
	for u := range net {
		if s.overdrawnInHistory(u, recorded) {
			for _, c := range corrections {
				c.payment.Revisions = c.payment.Revisions[:len(c.payment.Revisions)-1]
			}
			return nil, &apiError{409, "historical_overdraft", "the correction would overdraw a wallet in the past"}
		}
	}
	for u, delta := range net {
		u.Balance += delta
	}
	return revisions, nil
}

func (srv *Server) correctPayment(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	payment := st.paymentsByID[r.PathValue("payment_id")]
	if payment == nil {
		return nil, errNotFound("no such payment")
	}
	if payment.FromUserID != user.ID {
		return nil, errForbidden("only the sender may correct this payment")
	}
	c, apiErr := parseCorrection(body, now)
	if apiErr != nil {
		return nil, apiErr
	}
	c.payment = payment
	if payment.immutable() || payment.SettlementID != nil {
		return nil, errLinkedImmutable()
	}
	if apiErr := st.checkRevisionAndRefunds(c); apiErr != nil {
		return nil, apiErr
	}
	revisions, apiErr := st.applyCorrections([]correction{c}, nil, now)
	if apiErr != nil {
		return nil, apiErr
	}
	return revisions[0].view(payment.ID), nil
}

const maxBatchCorrections = 32

// parseBatchItem validates one batch entry against the payment it names.
func (s *State) parseBatchItem(item any, now time.Time) (correction, *apiError) {
	entry, ok := item.(map[string]any)
	if !ok {
		return correction{}, errValidation("each correction must be an object")
	}
	c, apiErr := parseCorrection(entry, now)
	if apiErr != nil {
		return c, apiErr
	}
	if c.payment = s.paymentsByID[entry["payment_id"].(string)]; c.payment == nil {
		return c, errNotFound("no such payment")
	}
	if c.payment.immutable() {
		return c, errLinkedImmutable()
	}
	return c, s.checkRevisionAndRefunds(c)
}

// checkSettlementsWhole requires every touched settlement to be corrected in full, at one instant.
func (s *State) checkSettlementsWhole(corrections []correction) *apiError {
	included := map[string]bool{}
	effectiveBySettlement := map[string]time.Time{}
	for _, c := range corrections {
		included[c.payment.ID] = true
	}
	for _, c := range corrections {
		settlement := c.payment.SettlementID
		if settlement == nil {
			continue
		}
		for _, member := range s.Payments {
			if member.SettlementID != nil && *member.SettlementID == *settlement && !included[member.ID] {
				return &apiError{422, "incomplete_settlement", "every member of the settlement must be corrected together"}
			}
		}
	}
	for _, c := range corrections {
		settlement := c.payment.SettlementID
		if settlement == nil {
			continue
		}
		if first, seen := effectiveBySettlement[*settlement]; seen && !first.Equal(c.effective) {
			return errValidation("members of one settlement must share one effective_at")
		}
		effectiveBySettlement[*settlement] = c.effective
	}
	return nil
}

func (srv *Server) createCorrectionBatch(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	items, ok := body["corrections"].([]any)
	if !ok || len(items) < 1 || len(items) > maxBatchCorrections {
		return nil, errValidation("corrections must contain 1 to 32 entries")
	}
	seen := map[string]bool{}
	for _, item := range items {
		entry, _ := item.(map[string]any)
		paymentID, ok := entry["payment_id"].(string)
		if !ok || seen[paymentID] {
			return nil, errValidation("each correction needs a distinct payment_id")
		}
		seen[paymentID] = true
	}

	corrections := make([]correction, len(items))
	for i, item := range items {
		var apiErr *apiError
		if corrections[i], apiErr = st.parseBatchItem(item, now); apiErr != nil {
			return nil, apiErr
		}
	}
	if apiErr := st.checkSettlementsWhole(corrections); apiErr != nil {
		return nil, apiErr
	}
	batchID := newID("cb_")
	revisions, apiErr := st.applyCorrections(corrections, &batchID, now)
	if apiErr != nil {
		return nil, apiErr
	}
	views := make([]revisionView, len(revisions))
	for i, revision := range revisions {
		views[i] = revision.view(corrections[i].payment.ID)
	}
	return map[string]any{"correction_batch_id": batchID, "recorded_at": revisions[0].RecordedAt, "revisions": views}, nil
}

func (srv *Server) refundPayment(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	target := st.paymentsByID[r.PathValue("payment_id")]
	if target == nil {
		return nil, errNotFound("no such payment")
	}
	if target.ToUserID != user.ID {
		return nil, errForbidden("only the receiver may refund this payment")
	}
	amount, apiErr := amountField(body, "amount")
	if apiErr != nil {
		return nil, apiErr
	}
	if target.RefundOf != nil {
		return nil, &apiError{422, "invalid_refund_target", "a refund cannot itself be refunded"}
	}
	if st.refundedAmount(target)+amount > target.latestRevision().Amount {
		return nil, errRefundExceeds()
	}
	if st.available(user, now) < amount {
		return nil, errInsufficientFunds()
	}
	targetID := target.ID
	refund := st.transfer(user, st.usersByID[target.FromUserID], amount, target.Note, target.Visibility,
		paymentLink{refundOf: &targetID}, now)
	return refund.view(st.Currency), nil
}
