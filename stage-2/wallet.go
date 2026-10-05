package main

import (
	"net/http"
	"time"
)

func (srv *Server) me(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	held := st.heldBy(user.ID, time.Now())
	writeJSON(w, http.StatusOK, map[string]any{
		"user_id": user.ID, "display_name": user.DisplayName, "handle": user.Handle,
		"balance": user.Balance, "total": user.Balance, "available": user.Balance - held, "held": held,
		"currency": st.Currency, "minor_units": st.MinorUnits,
	})
}

// lookupHandle validates a handle field value and resolves it to a user.
func (st *State) lookupHandle(field, handle string) (*User, *apiError) {
	if apiErr := handleValue(field, handle); apiErr != nil {
		return nil, apiErr
	}
	u := st.usersByHandle[handle]
	if u == nil {
		return nil, errNotFound("no user has the handle " + handle)
	}
	return u, nil
}

func (srv *Server) createPayment(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	toHandle, apiErr := requiredString(body, "to_handle")
	if apiErr != nil {
		return nil, apiErr
	}
	if apiErr := handleValue("to_handle", toHandle); apiErr != nil {
		return nil, apiErr
	}
	amount, apiErr := amountField(body, "amount")
	if apiErr != nil {
		return nil, apiErr
	}
	note, apiErr := noteField(body)
	if apiErr != nil {
		return nil, apiErr
	}
	visibility, apiErr := visibilityField(body)
	if apiErr != nil {
		return nil, apiErr
	}
	to, apiErr := st.lookupHandle("to_handle", toHandle)
	if apiErr != nil {
		return nil, apiErr
	}
	if to.ID == user.ID {
		return nil, &apiError{422, "self_payment", "you cannot pay yourself"}
	}
	if st.available(user, now) < amount {
		return nil, errInsufficientFunds()
	}
	return st.transfer(user, to, amount, note, visibility, paymentLink{}, now).view(st.Currency), nil
}

func (srv *Server) createRequest(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	payerHandle, apiErr := requiredString(body, "payer_handle")
	if apiErr != nil {
		return nil, apiErr
	}
	if apiErr := handleValue("payer_handle", payerHandle); apiErr != nil {
		return nil, apiErr
	}
	amount, apiErr := amountField(body, "amount")
	if apiErr != nil {
		return nil, apiErr
	}
	note, apiErr := noteField(body)
	if apiErr != nil {
		return nil, apiErr
	}
	payer, apiErr := st.lookupHandle("payer_handle", payerHandle)
	if apiErr != nil {
		return nil, apiErr
	}
	if payer.ID == user.ID {
		return nil, &apiError{422, "self_request", "you cannot request money from yourself"}
	}
	return st.createRequest(user, payer, amount, note, now).view(st.Currency), nil
}

func (srv *Server) payRequest(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	visibility, apiErr := visibilityField(body)
	if apiErr != nil {
		return nil, apiErr
	}
	req := st.requestsByID[r.PathValue("id")]
	if req == nil {
		return nil, errNotFound("no such request")
	}
	if req.PayerID != user.ID {
		return nil, errForbidden("only the payer may pay this request")
	}
	if req.Status != statusPending {
		return nil, errNotPending()
	}
	if st.available(user, now) < req.Amount {
		return nil, errInsufficientFunds()
	}
	requestID := req.ID
	payment := st.transfer(user, st.usersByID[req.RequesterID], req.Amount, req.Note, visibility, paymentLink{requestID: &requestID}, now)
	req.Status = statusPaid
	req.PaymentID = &payment.ID
	return payment.view(st.Currency), nil
}

// requestForParty finds a request and checks the caller holds the given role on it.
func (st *State) requestForParty(w http.ResponseWriter, r *http.Request, user *User, role func(*Request) string, roleName string) *Request {
	req := st.requestsByID[r.PathValue("id")]
	if req == nil {
		writeError(w, errNotFound("no such request"))
		return nil
	}
	if role(req) != user.ID {
		writeError(w, errForbidden("only the "+roleName+" may do this"))
		return nil
	}
	return req
}

func payerOf(req *Request) string     { return req.PayerID }
func requesterOf(req *Request) string { return req.RequesterID }

func (srv *Server) declineRequest(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	srv.closeRequest(w, r, st, user, payerOf, "payer", statusDeclined)
}

func (srv *Server) cancelRequest(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	srv.closeRequest(w, r, st, user, requesterOf, "requester", statusCancelled)
}

// closeRequest moves a pending request to a final status; repeating it is not an error.
func (srv *Server) closeRequest(w http.ResponseWriter, r *http.Request, st *State, user *User, role func(*Request) string, roleName, target string) {
	req := st.requestForParty(w, r, user, role, roleName)
	if req == nil {
		return
	}
	switch req.Status {
	case statusPending:
		req.Status = target
	case target:
	default:
		writeError(w, errNotPending())
		return
	}
	writeJSON(w, http.StatusOK, req.view(st.Currency))
}

func (srv *Server) listRequests(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	limit, offset, apiErr := pagination(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	filter, apiErr := parseListFilter(r, validRequestStatus)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}

	matches := []requestView{}
	for i := len(st.Requests) - 1; i >= 0; i-- {
		req := st.Requests[i]
		if !filter.matches(req.PayerID == user.ID, req.RequesterID == user.ID, req.Status) {
			continue
		}
		matches = append(matches, req.view(st.Currency))
	}
	page, hasMore := paginate(matches, limit, offset)
	writeJSON(w, http.StatusOK, map[string]any{"requests": page, "has_more": hasMore})
}

func (srv *Server) activity(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	limit, offset, apiErr := pagination(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	visible := []paymentView{}
	for i := len(st.Payments) - 1; i >= 0; i-- {
		p := st.Payments[i]
		if p.Visibility == "public" || p.FromUserID == user.ID || p.ToUserID == user.ID {
			visible = append(visible, p.view(st.Currency))
		}
	}
	page, hasMore := paginate(visible, limit, offset)
	writeJSON(w, http.StatusOK, map[string]any{"payments": page, "has_more": hasMore})
}

type splitShare struct {
	Handle string `json:"handle"`
	Amount int64  `json:"amount"`
}

// splitShares divides amount into n whole shares differing by at most one,
// with the larger shares first.
func splitShares(amount int64, n int) []int64 {
	shares := make([]int64, n)
	base, remainder := amount/int64(n), amount%int64(n)
	for i := range shares {
		shares[i] = base
		if int64(i) < remainder {
			shares[i]++
		}
	}
	return shares
}

func (srv *Server) createSplit(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	amount, apiErr := amountField(body, "amount")
	if apiErr != nil {
		return nil, apiErr
	}
	rawHandles, present := body["participant_handles"]
	if !present {
		return nil, errValidation("participant_handles is required")
	}
	list, ok := rawHandles.([]any)
	if !ok {
		return nil, errMalformed("participant_handles must be an array of strings")
	}
	if len(list) == 0 {
		return nil, errValidation("participant_handles must not be empty")
	}
	handles := make([]string, len(list))
	seen := map[string]bool{}
	for i, item := range list {
		handle, ok := item.(string)
		if !ok {
			return nil, errMalformed("participant_handles must be an array of strings")
		}
		if apiErr := handleValue("participant_handles", handle); apiErr != nil {
			return nil, apiErr
		}
		if seen[handle] {
			return nil, errValidation("participant_handles contains a duplicate handle")
		}
		seen[handle] = true
		handles[i] = handle
	}
	note, apiErr := noteField(body)
	if apiErr != nil {
		return nil, apiErr
	}
	participants := make([]*User, len(handles))
	for i, handle := range handles {
		if participants[i], apiErr = st.lookupHandle("participant_handles", handle); apiErr != nil {
			return nil, apiErr
		}
	}

	shares := []splitShare{}
	requests := []requestView{}
	for i, amount := range splitShares(amount, len(participants)) {
		participant := participants[i]
		shares = append(shares, splitShare{Handle: participant.Handle, Amount: amount})
		if participant.ID != user.ID {
			requests = append(requests, st.createRequest(user, participant, amount, note, now).view(st.Currency))
		}
	}
	return map[string]any{
		"split_id": newID("split_"), "amount": amount, "currency": st.Currency, "note": note,
		"shares": shares, "requests": requests, "created_at": formatTime(now),
	}, nil
}

type settlementTransfer struct {
	from, to   *User
	amount     int64
	note       string
	visibility string
}

// parseTransfer validates one settlement entry. Shape errors are 422 (§11).
func (st *State) parseTransfer(item any) (settlementTransfer, *apiError) {
	var t settlementTransfer
	entry, ok := item.(map[string]any)
	if !ok {
		return t, errValidation("each transfer must be an object")
	}
	fromHandle, fromOK := entry["from_handle"].(string)
	toHandle, toOK := entry["to_handle"].(string)
	if !fromOK || !toOK {
		return t, errValidation("each transfer needs from_handle and to_handle strings")
	}
	if apiErr := handleValue("from_handle", fromHandle); apiErr != nil {
		return t, apiErr
	}
	if apiErr := handleValue("to_handle", toHandle); apiErr != nil {
		return t, apiErr
	}
	var apiErr *apiError
	if t.amount, apiErr = amountField(entry, "amount"); apiErr != nil {
		return t, apiErr
	}
	if t.note, apiErr = noteField(entry); apiErr != nil {
		return t, apiErr
	}
	if t.visibility, apiErr = visibilityField(entry); apiErr != nil {
		return t, apiErr
	}
	if t.from, apiErr = st.lookupHandle("from_handle", fromHandle); apiErr != nil {
		return t, apiErr
	}
	if t.to, apiErr = st.lookupHandle("to_handle", toHandle); apiErr != nil {
		return t, apiErr
	}
	if t.from.ID == t.to.ID {
		return t, &apiError{422, "self_payment", "a transfer cannot go to its own sender"}
	}
	return t, nil
}

func (srv *Server) createSettlement(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	items, ok := body["transfers"].([]any)
	if !ok {
		return nil, errValidation("transfers must be an array")
	}
	if len(items) < 1 || len(items) > 32 {
		return nil, errValidation("transfers must contain 1 to 32 entries")
	}
	transfers := make([]settlementTransfer, len(items))
	for i, item := range items {
		var apiErr *apiError
		if transfers[i], apiErr = st.parseTransfer(item); apiErr != nil {
			return nil, apiErr
		}
	}

	net := map[*User]int64{}
	for _, t := range transfers {
		net[t.from] -= t.amount
		net[t.to] += t.amount
	}
	for u, delta := range net {
		if st.available(u, now)+delta < 0 {
			return nil, errInsufficientFunds()
		}
	}
	for u, delta := range net {
		u.Balance += delta
	}

	settlementID := newID("stl_")
	payments := make([]paymentView, len(transfers))
	for i, t := range transfers {
		payments[i] = st.recordPayment(t.from, t.to, t.amount, t.note, t.visibility, paymentLink{settlementID: &settlementID}, now).view(st.Currency)
	}
	return map[string]any{"settlement_id": settlementID, "committed_at": formatTime(now), "payments": payments}, nil
}
