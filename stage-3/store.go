package main

import (
	"errors"
	"strings"
	"sync"
	"time"
)

// Store holds all service state in memory. Every read and write happens under mu,
// which makes each operation atomic with respect to all others.
type Store struct {
	mu    sync.Mutex
	state *State
}

// State is the complete service state; it is also the export format.
type State struct {
	Currency   string            `json:"currency"`
	MinorUnits int               `json:"minor_units"`
	Users      []*User           `json:"users"`
	Tokens     map[string]string `json:"tokens"`
	Payments   []*Payment        `json:"payments"`
	Requests   []*Request        `json:"requests"`
	// Authorizations and AuthorizationTTL are absent from stage-1 exports.
	Authorizations   []*Authorization     `json:"authorizations"`
	AuthorizationTTL int64                `json:"authorization_ttl_seconds"`
	Operators        []string             `json:"settlement_operator_ids"`
	Idempotency      []*IdempotencyRecord `json:"idempotency"`
	Seq              int64                `json:"seq"`

	usersByID     map[string]*User
	usersByHandle map[string]*User
	usersByEmail  map[string]*User
	requestsByID  map[string]*Request
	authsByID     map[string]*Authorization
	paymentIDs    map[string]bool
	operators     map[string]bool
	idempotency   map[string]*IdempotencyRecord
}

func emptyState() *State {
	s := &State{Currency: "EUR", MinorUnits: 2, Tokens: map[string]string{}}
	if err := s.buildIndexes(); err != nil {
		panic(err)
	}
	return s
}

func emailKey(email string) string {
	return strings.ToLower(email)
}

// buildIndexes derives lookup maps and checks that the state is internally consistent.
func (s *State) buildIndexes() error {
	if s.MinorUnits != 0 && s.MinorUnits != 2 && s.MinorUnits != 3 {
		return errors.New("minor_units must be 0, 2 or 3")
	}
	if s.Currency == "" {
		return errors.New("currency is required")
	}
	if s.Tokens == nil {
		s.Tokens = map[string]string{}
	}
	s.usersByID = map[string]*User{}
	s.usersByHandle = map[string]*User{}
	s.usersByEmail = map[string]*User{}
	s.requestsByID = map[string]*Request{}
	s.paymentIDs = map[string]bool{}
	s.authsByID = map[string]*Authorization{}
	s.operators = map[string]bool{}
	s.idempotency = map[string]*IdempotencyRecord{}

	for _, u := range s.Users {
		if u == nil || u.ID == "" || len(u.ID) > maxIDLength {
			return errors.New("invalid user id")
		}
		if !handlePattern.MatchString(u.Handle) {
			return errors.New("invalid handle " + u.Handle)
		}
		if u.Balance < 0 {
			return errors.New("negative balance")
		}
		if s.usersByID[u.ID] != nil || s.usersByHandle[u.Handle] != nil || s.usersByEmail[emailKey(u.Email)] != nil {
			return errors.New("duplicate user id, handle or email")
		}
		s.usersByID[u.ID] = u
		s.usersByHandle[u.Handle] = u
		s.usersByEmail[emailKey(u.Email)] = u
	}
	for _, userID := range s.Tokens {
		if s.usersByID[userID] == nil {
			return errors.New("token for unknown user")
		}
	}
	for _, p := range s.Payments {
		if p == nil || p.ID == "" || len(p.ID) > maxIDLength || s.paymentIDs[p.ID] {
			return errors.New("invalid payment id")
		}
		if s.usersByID[p.FromUserID] == nil || s.usersByID[p.ToUserID] == nil {
			return errors.New("payment references unknown user")
		}
		if p.Visibility != "public" && p.Visibility != "private" {
			return errors.New("invalid visibility")
		}
		s.paymentIDs[p.ID] = true
	}
	for _, r := range s.Requests {
		if r == nil || r.ID == "" || len(r.ID) > maxIDLength || s.requestsByID[r.ID] != nil {
			return errors.New("invalid request id")
		}
		if s.usersByID[r.RequesterID] == nil || s.usersByID[r.PayerID] == nil {
			return errors.New("request references unknown user")
		}
		if !validRequestStatus(r.Status) {
			return errors.New("invalid request status")
		}
		s.requestsByID[r.ID] = r
	}
	if s.AuthorizationTTL == 0 {
		s.AuthorizationTTL = defaultAuthorizationTTL
	}
	if s.AuthorizationTTL < 0 {
		return errors.New("authorization_ttl_seconds must be positive")
	}
	for _, a := range s.Authorizations {
		if a == nil || a.ID == "" || len(a.ID) > maxIDLength || s.authsByID[a.ID] != nil {
			return errors.New("invalid authorization id")
		}
		if s.usersByID[a.FromUserID] == nil || s.usersByID[a.ToUserID] == nil {
			return errors.New("authorization references unknown user")
		}
		if !validAuthorizationStatus(a.Status) || a.Amount < 1 || a.CapturedAmount < 0 || a.CapturedAmount > a.Amount {
			return errors.New("invalid authorization amounts or status")
		}
		expires, err := time.Parse(time.RFC3339, a.ExpiresAt)
		if err != nil {
			return errors.New("invalid authorization expires_at")
		}
		a.expires = expires
		s.authsByID[a.ID] = a
	}
	now := time.Now()
	for _, u := range s.Users {
		if s.heldBy(u.ID, now) > u.Balance {
			return errors.New("open holds exceed the balance of " + u.ID)
		}
	}
	for _, id := range s.Operators {
		s.operators[id] = true
	}
	for _, rec := range s.Idempotency {
		if rec == nil || s.usersByID[rec.UserID] == nil || len(rec.Response) == 0 {
			return errors.New("invalid idempotency record")
		}
		s.idempotency[rec.scope()] = rec
	}
	return nil
}

// heldBy is the sum of the user's open holds.
func (s *State) heldBy(userID string, now time.Time) int64 {
	var held int64
	for _, a := range s.Authorizations {
		if a.FromUserID == userID {
			held += a.heldAt(now)
		}
	}
	return held
}

// available is what the user can spend: total minus open holds.
func (s *State) available(u *User, now time.Time) int64 {
	return u.Balance - s.heldBy(u.ID, now)
}

func (s *State) nextSeq() int64 {
	s.Seq++
	return s.Seq
}

func (s *State) addUser(u *User) {
	s.Users = append(s.Users, u)
	s.usersByID[u.ID] = u
	s.usersByHandle[u.Handle] = u
	s.usersByEmail[emailKey(u.Email)] = u
}

func (s *State) issueToken(userID string) string {
	token := newToken()
	s.Tokens[token] = userID
	return token
}

func (s *State) newPaymentID() string {
	for {
		id := newID("pay_")
		if !s.paymentIDs[id] {
			return id
		}
	}
}

func (s *State) newRequestID() string {
	for {
		id := newID("req_")
		if s.requestsByID[id] == nil {
			return id
		}
	}
}

// transfer moves money between two wallets and records the payment.
// The caller must already have checked that the sender can afford it.
func (s *State) transfer(from, to *User, amount int64, note, visibility string, link paymentLink, at time.Time) *Payment {
	from.Balance -= amount
	to.Balance += amount
	return s.recordPayment(from, to, amount, note, visibility, link, at)
}

// paymentLink names the request, settlement or authorization a payment belongs to, if any.
type paymentLink struct {
	requestID, settlementID, authorizationID *string
}

// recordPayment appends a payment record without touching balances.
func (s *State) recordPayment(from, to *User, amount int64, note, visibility string, link paymentLink, at time.Time) *Payment {
	p := &Payment{
		ID: s.newPaymentID(), Seq: s.nextSeq(),
		FromUserID: from.ID, FromHandle: from.Handle, ToUserID: to.ID, ToHandle: to.Handle,
		Amount: amount, Note: note, Visibility: visibility,
		RequestID: link.requestID, SettlementID: link.settlementID, AuthorizationID: link.authorizationID,
		CreatedAt: formatTime(at),
	}
	s.Payments = append(s.Payments, p)
	s.paymentIDs[p.ID] = true
	return p
}

func (s *State) newAuthorizationID() string {
	for {
		id := newID("auth_")
		if s.authsByID[id] == nil {
			return id
		}
	}
}

func (s *State) createAuthorization(from, to *User, amount int64, note, visibility string, at time.Time) *Authorization {
	// Millisecond precision, so the deadline read back from expires_at is the one enforced.
	at = at.Truncate(time.Millisecond)
	expires := at.Add(time.Duration(s.AuthorizationTTL) * time.Second)
	a := &Authorization{
		ID: s.newAuthorizationID(), Seq: s.nextSeq(),
		FromUserID: from.ID, FromHandle: from.Handle, ToUserID: to.ID, ToHandle: to.Handle,
		Amount: amount, Note: note, Visibility: visibility, Status: authOpen,
		ExpiresAt: formatPreciseTime(expires), PaymentIDs: []string{}, CreatedAt: formatPreciseTime(at),
		expires: expires,
	}
	s.Authorizations = append(s.Authorizations, a)
	s.authsByID[a.ID] = a
	return a
}

func (s *State) createRequest(requester, payer *User, amount int64, note string, at time.Time) *Request {
	r := &Request{
		ID: s.newRequestID(), Seq: s.nextSeq(),
		RequesterID: requester.ID, RequesterHandle: requester.Handle,
		PayerID: payer.ID, PayerHandle: payer.Handle,
		Amount: amount, Note: note, Status: statusPending, CreatedAt: formatTime(at),
	}
	s.Requests = append(s.Requests, r)
	s.requestsByID[r.ID] = r
	return r
}

func (s *State) remember(rec *IdempotencyRecord) {
	s.Idempotency = append(s.Idempotency, rec)
	s.idempotency[rec.scope()] = rec
}
