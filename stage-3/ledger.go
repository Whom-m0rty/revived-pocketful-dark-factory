package main

import (
	"net/http"
	"sort"
	"time"
	"unicode/utf8"
)

// A ledger view answers "what did the service know at knownAt about balances as they
// stood at asOf": each payment counts with its latest revision recorded at or before
// knownAt, if that revision's effective time is at or before asOf.

// totalAt is the user's total balance in the view.
func (s *State) totalAt(u *User, asOf, knownAt time.Time) int64 {
	total := u.OpeningBalance
	for _, p := range s.Payments {
		if p.FromUserID != u.ID && p.ToUserID != u.ID {
			continue
		}
		if r := p.revisionKnownAt(knownAt); r != nil && !r.effective.After(asOf) {
			total += p.deltaFor(u.ID, r.Amount)
		}
	}
	return total
}

// heldInView is the amount this hold reserves in the view. Expiry is known as soon as
// creation is; captures and closing are known from the instant they happen.
func (s *State) heldInView(a *Authorization, asOf, knownAt time.Time) int64 {
	happened := func(t time.Time) bool { return !t.After(asOf) && !t.After(knownAt) }
	if !happened(a.created) || !a.expires.After(asOf) {
		return 0
	}
	if a.closed != nil && happened(*a.closed) {
		return 0
	}
	held := a.Amount
	for _, id := range a.PaymentIDs {
		if capture := s.paymentsByID[id]; happened(capture.created) {
			held -= capture.Amount
		}
	}
	return held
}

func (s *State) heldInViewBy(u *User, asOf, knownAt time.Time) int64 {
	var held int64
	for _, a := range s.Authorizations {
		if a.FromUserID == u.ID {
			held += s.heldInView(a, asOf, knownAt)
		}
	}
	return held
}

// timeQuery reads an optional RFC 3339 instant query parameter; present but invalid is 422.
func timeQuery(r *http.Request, name string) (value string, t time.Time, present bool, apiErr *apiError) {
	values, present := r.URL.Query()[name]
	if !present {
		return "", time.Time{}, false, nil
	}
	t, ok := parseInstant(values[0])
	if !ok {
		return "", time.Time{}, true, errValidation(name + " must be an RFC 3339 instant with an offset")
	}
	return values[0], t, true, nil
}

func (srv *Server) me(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	now := currentTime()
	asOfText, asOf, hasAsOf, apiErr := timeQuery(r, "as_of")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	knownAtText, knownAt, hasKnownAt, apiErr := timeQuery(r, "known_at")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	if !hasAsOf {
		asOf = now
	}
	if !hasKnownAt {
		knownAt = now
	}
	total := st.totalAt(user, asOf, knownAt)
	held := st.heldInViewBy(user, asOf, knownAt)
	response := map[string]any{
		"user_id": user.ID, "display_name": user.DisplayName, "handle": user.Handle,
		"balance": total, "total": total, "available": total - held, "held": held,
		"currency": st.Currency, "minor_units": st.MinorUnits,
	}
	if hasAsOf {
		response["as_of"] = asOfText
	}
	if hasKnownAt {
		response["known_at"] = knownAtText
	}
	writeJSON(w, http.StatusOK, response)
}

// ---------- statements ----------

type statementEntry struct {
	Payment      paymentView `json:"payment"`
	Delta        int64       `json:"delta"`
	BalanceAfter int64       `json:"balance_after"`
	Revision     int64       `json:"revision"`
	EffectiveAt  string      `json:"effective_at"`
	RecordedAt   string      `json:"recorded_at"`
}

// statementSnapshot is a computed statement, frozen so later pages read the same result.
type statementSnapshot struct {
	userID         string
	openingBalance int64
	closingBalance int64
	entries        []statementEntry
}

type selectedRevision struct {
	payment  *Payment
	revision *Revision
}

// buildStatement computes the caller's statement for the window [from, to) in the
// knownAt view. A nil from means the opening of the wallet.
func (s *State) buildStatement(u *User, from *time.Time, to, knownAt time.Time) *statementSnapshot {
	var selected []selectedRevision
	for _, p := range s.Payments {
		if p.FromUserID != u.ID && p.ToUserID != u.ID {
			continue
		}
		if r := p.revisionKnownAt(knownAt); r != nil {
			selected = append(selected, selectedRevision{p, r})
		}
	}
	sort.Slice(selected, func(i, j int) bool {
		a, b := selected[i], selected[j]
		if !a.revision.effective.Equal(b.revision.effective) {
			return a.revision.effective.Before(b.revision.effective)
		}
		return a.payment.ID < b.payment.ID
	})

	snapshot := &statementSnapshot{userID: u.ID, openingBalance: u.OpeningBalance, entries: []statementEntry{}}
	balance := u.OpeningBalance
	for _, sel := range selected {
		effective := sel.revision.effective
		if !effective.Before(to) {
			break
		}
		delta := sel.payment.deltaFor(u.ID, sel.revision.Amount)
		balance += delta
		if from != nil && effective.Before(*from) {
			snapshot.openingBalance = balance
			continue
		}
		view := sel.payment.view(s.Currency)
		view.Amount = sel.revision.Amount
		snapshot.entries = append(snapshot.entries, statementEntry{
			Payment: view, Delta: delta, BalanceAfter: balance, Revision: sel.revision.Revision,
			EffectiveAt: sel.revision.EffectiveAt, RecordedAt: sel.revision.RecordedAt,
		})
	}
	snapshot.closingBalance = balance
	return snapshot
}

func (srv *Server) statement(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	now := currentTime()
	_, from, hasFrom, apiErr := timeQuery(r, "from")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	_, to, hasTo, apiErr := timeQuery(r, "to")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	_, knownAt, hasKnownAt, apiErr := timeQuery(r, "known_at")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	token, hasSnapshot := r.URL.Query()["snapshot"]
	if hasSnapshot && (hasFrom || hasTo || hasKnownAt) {
		writeError(w, errValidation("only limit and offset may accompany snapshot"))
		return
	}
	limit, offset, apiErr := pagination(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}

	var snapshotID string
	var snapshot *statementSnapshot
	if hasSnapshot {
		snapshotID = token[0]
		snapshot = st.snapshots[snapshotID]
		if snapshot == nil || snapshot.userID != user.ID {
			writeError(w, errNotFound("no such statement snapshot"))
			return
		}
	} else {
		if !hasTo {
			to = now
		}
		if !hasKnownAt {
			knownAt = now
		}
		var fromPtr *time.Time
		if hasFrom {
			if from.After(to) {
				writeError(w, errValidation("from must not be after to"))
				return
			}
			fromPtr = &from
		}
		snapshot = st.buildStatement(user, fromPtr, to, knownAt)
		snapshotID = newID("stmt_")
		st.snapshots[snapshotID] = snapshot
	}

	page, hasMore := paginate(snapshot.entries, limit, offset)
	writeJSON(w, http.StatusOK, map[string]any{
		"opening_balance": snapshot.openingBalance, "entries": page,
		"closing_balance": snapshot.closingBalance, "has_more": hasMore, "snapshot": snapshotID,
	})
}

// ---------- corrections ----------

// correctionFields validates a correction body; every invalid input is 422.
func correctionFields(body map[string]any, now time.Time) (expected, amount int64, effectiveText string, effective time.Time, reason string, apiErr *apiError) {
	expected, ok := integralValue(body["expected_revision"])
	if !ok || expected < 1 {
		return 0, 0, "", time.Time{}, "", errValidation("expected_revision must be a positive integer")
	}
	amount, ok = integralValue(body["amount"])
	if !ok || amount < 0 || amount > maxAmount {
		return 0, 0, "", time.Time{}, "", errValidation("amount must be an integer from 0 to 1000000000")
	}
	effectiveText, _ = body["effective_at"].(string)
	effective, ok = parseInstant(effectiveText)
	if !ok || effective.After(now) {
		return 0, 0, "", time.Time{}, "", errValidation("effective_at must be an RFC 3339 instant that is not in the future")
	}
	reason, ok = body["reason"].(string)
	if n := utf8.RuneCountInString(reason); !ok || n < 1 || n > maxNoteLength {
		return 0, 0, "", time.Time{}, "", errValidation("reason must be 1 to 200 characters")
	}
	return expected, amount, effectiveText, effective, reason, nil
}

func (srv *Server) correctPayment(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	payment := st.paymentsByID[r.PathValue("payment_id")]
	if payment == nil {
		return nil, errNotFound("no such payment")
	}
	if payment.FromUserID != user.ID {
		return nil, errForbidden("only the sender may correct this payment")
	}
	expected, amount, effectiveText, effective, reason, apiErr := correctionFields(body, now)
	if apiErr != nil {
		return nil, apiErr
	}
	if payment.linked() {
		return nil, &apiError{422, "linked_payment_immutable", "settlement members and captures cannot be corrected"}
	}
	latest := payment.latestRevision()
	if expected != latest.Revision {
		return nil, &apiError{409, "stale_revision", "the payment has been corrected since that revision"}
	}

	sender, receiver := st.usersByID[payment.FromUserID], st.usersByID[payment.ToUserID]
	change := amount - latest.Amount
	if (change > 0 && st.available(sender, now) < change) || (change < 0 && st.available(receiver, now) < -change) {
		return nil, errInsufficientFunds()
	}

	recorded := now
	if !recorded.After(latest.recorded) {
		recorded = latest.recorded.Add(time.Millisecond)
	}
	revision := &Revision{Revision: latest.Revision + 1, Amount: amount, EffectiveAt: effectiveText,
		RecordedAt: formatTime(recorded), Reason: reason, effective: effective, recorded: recorded}
	payment.Revisions = append(payment.Revisions, revision)
	if st.overdrawnInHistory(sender, recorded) || st.overdrawnInHistory(receiver, recorded) {
		payment.Revisions = payment.Revisions[:len(payment.Revisions)-1]
		return nil, &apiError{409, "historical_overdraft", "the correction would overdraw a wallet in the past"}
	}
	sender.Balance -= change
	receiver.Balance += change
	return revision.view(payment.ID), nil
}

// overdrawnInHistory reports whether the user's total or available balance is negative at
// any effective or hold-event instant up to knownAt, under the latest revisions.
func (s *State) overdrawnInHistory(u *User, knownAt time.Time) bool {
	boundaries := []time.Time{knownAt}
	for _, p := range s.Payments {
		if p.FromUserID == u.ID || p.ToUserID == u.ID {
			if r := p.revisionKnownAt(knownAt); r != nil {
				boundaries = append(boundaries, r.effective)
			}
		}
	}
	for _, a := range s.Authorizations {
		if a.FromUserID != u.ID {
			continue
		}
		boundaries = append(boundaries, a.created, a.expires)
		if a.closed != nil {
			boundaries = append(boundaries, *a.closed)
		}
		for _, id := range a.PaymentIDs {
			boundaries = append(boundaries, s.paymentsByID[id].created)
		}
	}
	for _, at := range boundaries {
		if at.After(knownAt) {
			continue
		}
		total := s.totalAt(u, at, knownAt)
		if total < 0 || total-s.heldInViewBy(u, at, knownAt) < 0 {
			return true
		}
	}
	return false
}

func (srv *Server) paymentRevisions(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	payment := st.paymentsByID[r.PathValue("payment_id")]
	if payment == nil || (payment.FromUserID != user.ID && payment.ToUserID != user.ID) {
		writeError(w, errNotFound("no such payment"))
		return
	}
	revisions := make([]revisionView, len(payment.Revisions))
	for i, revision := range payment.Revisions {
		revisions[i] = revision.view(payment.ID)
	}
	writeJSON(w, http.StatusOK, map[string]any{"revisions": revisions})
}

// ---------- ordering ----------

// paymentsNewestFirst orders payments by created_at, newest first; ties keep reverse insertion order.
func (s *State) paymentsNewestFirst() []*Payment {
	ordered := make([]*Payment, len(s.Payments))
	for i, p := range s.Payments {
		ordered[len(s.Payments)-1-i] = p
	}
	sort.SliceStable(ordered, func(i, j int) bool { return ordered[i].created.After(ordered[j].created) })
	return ordered
}

// authorizationsNewestFirst orders authorizations by created_at, newest first.
func (s *State) authorizationsNewestFirst() []*Authorization {
	ordered := make([]*Authorization, len(s.Authorizations))
	for i, a := range s.Authorizations {
		ordered[len(s.Authorizations)-1-i] = a
	}
	sort.SliceStable(ordered, func(i, j int) bool { return ordered[i].created.After(ordered[j].created) })
	return ordered
}
