package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"sync"
	"time"
)

// fixture is the body of POST /_test/reset (§4).
type fixture struct {
	Currency   *string          `json:"currency"`
	MinorUnits json.Number      `json:"minor_units"`
	Users      []fixtureUser    `json:"users"`
	Payments   []fixturePayment `json:"payments"`
	Requests   []fixtureRequest `json:"requests"`
	Operators  []string         `json:"settlement_operator_ids"`
}

type fixtureUser struct {
	ID          string      `json:"id"`
	Email       string      `json:"email"`
	Password    string      `json:"password"`
	DisplayName string      `json:"display_name"`
	Handle      string      `json:"handle"`
	Balance     json.Number `json:"balance"`
}

type fixturePayment struct {
	ID         string      `json:"id"`
	FromUserID string      `json:"from_user_id"`
	ToUserID   string      `json:"to_user_id"`
	Amount     json.Number `json:"amount"`
	Note       string      `json:"note"`
	Visibility *string     `json:"visibility"`
}

type fixtureRequest struct {
	ID          string      `json:"id"`
	RequesterID string      `json:"requester_id"`
	PayerID     string      `json:"payer_id"`
	Amount      json.Number `json:"amount"`
	Note        string      `json:"note"`
	Status      string      `json:"status"`
}

// stateFromFixture builds a fresh state. Seeded balances are taken as already net
// of seeded payments; seeded items get past timestamps in fixture order.
func stateFromFixture(f *fixture) (*State, *apiError) {
	if f.Currency == nil || f.Users == nil {
		return nil, errValidation("currency, minor_units and users are required")
	}
	minorUnits, ok := integralValue(f.MinorUnits)
	if !ok {
		return nil, errValidation("minor_units must be 0, 2 or 3")
	}
	st := &State{Currency: *f.Currency, MinorUnits: int(minorUnits), Tokens: map[string]string{}, Operators: f.Operators}

	hashes := make([]string, len(f.Users))
	var wg sync.WaitGroup
	for i, u := range f.Users {
		wg.Add(1)
		go func() {
			defer wg.Done()
			hashes[i] = hashPassword(u.Password)
		}()
	}
	wg.Wait()

	for i, u := range f.Users {
		balance, ok := integralValue(u.Balance)
		if !ok || balance < 0 {
			return nil, errValidation("user balances must be integers of zero or more")
		}
		st.Users = append(st.Users, &User{ID: u.ID, Email: u.Email, PasswordHash: hashes[i],
			DisplayName: u.DisplayName, Handle: u.Handle, Balance: balance})
	}

	seededAt := time.Now().Add(-time.Hour)
	for _, p := range f.Payments {
		amount, ok := integralValue(p.Amount)
		if !ok {
			return nil, errValidation("payment amounts must be integers")
		}
		visibility := "public"
		if p.Visibility != nil {
			visibility = *p.Visibility
		}
		seq := st.nextSeq()
		st.Payments = append(st.Payments, &Payment{ID: p.ID, Seq: seq, FromUserID: p.FromUserID, ToUserID: p.ToUserID,
			Amount: amount, Note: p.Note, Visibility: visibility, CreatedAt: formatTime(seededAt.Add(time.Duration(seq) * time.Second))})
	}
	for _, rq := range f.Requests {
		amount, ok := integralValue(rq.Amount)
		if !ok {
			return nil, errValidation("request amounts must be integers")
		}
		seq := st.nextSeq()
		st.Requests = append(st.Requests, &Request{ID: rq.ID, Seq: seq, RequesterID: rq.RequesterID, PayerID: rq.PayerID,
			Amount: amount, Note: rq.Note, Status: rq.Status, CreatedAt: formatTime(seededAt.Add(time.Duration(seq) * time.Second))})
	}

	if err := st.buildIndexes(); err != nil {
		return nil, errValidation("invalid fixture: " + err.Error())
	}
	for _, p := range st.Payments {
		p.FromHandle = st.usersByID[p.FromUserID].Handle
		p.ToHandle = st.usersByID[p.ToUserID].Handle
	}
	for _, rq := range st.Requests {
		rq.RequesterHandle = st.usersByID[rq.RequesterID].Handle
		rq.PayerHandle = st.usersByID[rq.PayerID].Handle
	}
	return st, nil
}

// decodeInto parses a body as a JSON object and decodes it strictly into v.
// Unparseable JSON is 400; a value that does not fit v is 422.
func decodeInto(data []byte, v any, disallowUnknown bool) *apiError {
	if _, apiErr := parseObject(data, false); apiErr != nil {
		return apiErr
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	if disallowUnknown {
		dec.DisallowUnknownFields()
	}
	if err := dec.Decode(v); err != nil {
		return errValidation("body does not have the expected structure: " + err.Error())
	}
	return nil
}

func (srv *Server) reset(w http.ResponseWriter, r *http.Request) {
	data, apiErr := readBody(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	var f fixture
	if apiErr := decodeInto(data, &f, false); apiErr != nil {
		writeError(w, apiErr)
		return
	}
	st, apiErr := stateFromFixture(&f)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	srv.store.mu.Lock()
	srv.store.state = st
	srv.store.mu.Unlock()
	writeNoContent(w)
}

const exportTrack = "pocketful"

type exportEnvelope struct {
	Track         string          `json:"track"`
	FormatVersion json.Number     `json:"format_version"`
	State         json.RawMessage `json:"state"`
}

func (srv *Server) export(w http.ResponseWriter, r *http.Request) {
	srv.store.mu.Lock()
	state := encodeJSON(srv.store.state)
	srv.store.mu.Unlock()
	writeJSON(w, http.StatusOK, exportEnvelope{Track: exportTrack, FormatVersion: "1", State: state})
}

func (srv *Server) importState(w http.ResponseWriter, r *http.Request) {
	data, apiErr := readBody(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	var envelope exportEnvelope
	if apiErr := decodeInto(data, &envelope, false); apiErr != nil {
		writeError(w, apiErr)
		return
	}
	if version, ok := integralValue(envelope.FormatVersion); envelope.Track != exportTrack || !ok || version != 1 {
		writeError(w, errValidation("track must be pocketful and format_version must be 1"))
		return
	}
	if len(envelope.State) == 0 {
		writeError(w, errValidation("state is required"))
		return
	}
	st := &State{}
	if apiErr := decodeInto(envelope.State, st, true); apiErr != nil {
		writeError(w, errValidation("state is not a valid export"))
		return
	}
	if err := st.buildIndexes(); err != nil {
		writeError(w, errValidation("state is not a valid export: "+err.Error()))
		return
	}
	srv.store.mu.Lock()
	srv.store.state = st
	srv.store.mu.Unlock()
	writeNoContent(w)
}
