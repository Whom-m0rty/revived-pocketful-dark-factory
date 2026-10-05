package main

import (
	"net/http"
	"time"
)

func errAuthorizationNotOpen() *apiError {
	return &apiError{409, "authorization_not_open", "the authorization is no longer open"}
}

func (srv *Server) createAuthorization(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
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
		return nil, &apiError{422, "self_payment", "you cannot reserve money for yourself"}
	}
	if st.available(user, now) < amount {
		return nil, errInsufficientFunds()
	}
	return st.createAuthorization(user, to, amount, note, visibility, now).view(st.Currency, now), nil
}

// captureFields reads the optional capture amount (0 when omitted) and final flag (default true).
func captureFields(body map[string]any) (amount int64, final bool, apiErr *apiError) {
	final = true
	if v, present := body["final"]; present {
		b, ok := v.(bool)
		if !ok {
			return 0, false, errMalformed("final must be a boolean")
		}
		final = b
	}
	if _, present := body["amount"]; present {
		if amount, apiErr = amountField(body, "amount"); apiErr != nil {
			return 0, false, apiErr
		}
	}
	return amount, final, nil
}

func (srv *Server) captureAuthorization(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError) {
	amount, final, apiErr := captureFields(body)
	if apiErr != nil {
		return nil, apiErr
	}
	auth := st.authsByID[r.PathValue("id")]
	if auth == nil {
		return nil, errNotFound("no such authorization")
	}
	if auth.ToUserID != user.ID {
		return nil, errForbidden("only the receiver may capture this authorization")
	}
	if auth.Status != authOpen {
		return nil, errAuthorizationNotOpen()
	}
	if auth.expiredAt(now) {
		return nil, &apiError{409, "authorization_expired", "the authorization has expired"}
	}
	remaining := auth.Amount - auth.CapturedAmount
	if amount == 0 {
		amount = remaining
	}
	if amount > remaining {
		return nil, &apiError{422, "capture_exceeds_authorization", "the amount is more than the authorization has left"}
	}

	authorizationID := auth.ID
	payment := st.transfer(st.usersByID[auth.FromUserID], user, amount, auth.Note, auth.Visibility,
		paymentLink{authorizationID: &authorizationID}, now)
	auth.CapturedAmount += amount
	auth.PaymentIDs = append(auth.PaymentIDs, payment.ID)
	if final || auth.CapturedAmount == auth.Amount {
		auth.Status = authCaptured
	}
	return payment.view(st.Currency), nil
}

func (srv *Server) voidAuthorization(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	now := time.Now()
	auth := st.authsByID[r.PathValue("id")]
	if auth == nil {
		writeError(w, errNotFound("no such authorization"))
		return
	}
	if auth.FromUserID != user.ID {
		writeError(w, errForbidden("only the payer may void this authorization"))
		return
	}
	switch auth.statusAt(now) {
	case authOpen:
		auth.Status = authVoided
	case authVoided:
	default:
		writeError(w, errAuthorizationNotOpen())
		return
	}
	writeJSON(w, http.StatusOK, auth.view(st.Currency, now))
}

func (srv *Server) listAuthorizations(w http.ResponseWriter, r *http.Request, st *State, user *User) {
	limit, offset, apiErr := pagination(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	filter, apiErr := parseListFilter(r, validAuthorizationStatus)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	now := time.Now()
	matches := []authorizationView{}
	for i := len(st.Authorizations) - 1; i >= 0; i-- {
		auth := st.Authorizations[i]
		if filter.matches(auth.ToUserID == user.ID, auth.FromUserID == user.ID, auth.statusAt(now)) {
			matches = append(matches, auth.view(st.Currency, now))
		}
	}
	page, hasMore := paginate(matches, limit, offset)
	writeJSON(w, http.StatusOK, map[string]any{"authorizations": page, "has_more": hasMore})
}
