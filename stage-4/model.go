package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"time"
)

var handlePattern = regexp.MustCompile(`^[a-z0-9_]{1,20}$`)

const (
	statusPending   = "pending"
	statusPaid      = "paid"
	statusDeclined  = "declined"
	statusCancelled = "cancelled"

	authOpen     = "open"
	authCaptured = "captured"
	authVoided   = "voided"
	authExpired  = "expired"

	defaultAuthorizationTTL = 600

	maxAmount     = 1000000000
	maxNoteLength = 200
	maxIDLength   = 64
)

// User is a wallet holder. PasswordHash is produced by hashPassword.
type User struct {
	ID           string `json:"id"`
	Email        string `json:"email"`
	PasswordHash string `json:"password_hash"`
	DisplayName  string `json:"display_name"`
	Handle       string `json:"handle"`
	Balance      int64  `json:"balance"`
	// OpeningBalance is what the wallet held before any recorded payment.
	OpeningBalance int64 `json:"opening_balance"`
}

// Payment is one completed money movement between two wallets.
type Payment struct {
	ID              string  `json:"id"`
	Seq             int64   `json:"seq"`
	FromUserID      string  `json:"from_user_id"`
	FromHandle      string  `json:"from_handle"`
	ToUserID        string  `json:"to_user_id"`
	ToHandle        string  `json:"to_handle"`
	Amount          int64   `json:"amount"`
	Note            string  `json:"note"`
	Visibility      string  `json:"visibility"`
	RequestID       *string `json:"request_id"`
	SettlementID    *string `json:"settlement_id"`
	AuthorizationID *string `json:"authorization_id"`
	RefundOf        *string `json:"refund_of"`
	CreatedAt       string  `json:"created_at"`
	// Revisions is the payment's amount history; revision 1 is the original payment.
	Revisions []*Revision `json:"revisions"`

	created time.Time
}

// Revision is one immutable entry in a payment's amount history.
type Revision struct {
	Revision    int64  `json:"revision"`
	Amount      int64  `json:"amount"`
	EffectiveAt string `json:"effective_at"`
	RecordedAt  string `json:"recorded_at"`
	Reason      string `json:"reason"`
	// CorrectionBatchID names the operator batch that recorded this revision, if any.
	CorrectionBatchID *string `json:"correction_batch_id"`

	effective time.Time
	recorded  time.Time
}

type revisionView struct {
	PaymentID         string  `json:"payment_id"`
	Revision          int64   `json:"revision"`
	Amount            int64   `json:"amount"`
	EffectiveAt       string  `json:"effective_at"`
	RecordedAt        string  `json:"recorded_at"`
	Reason            string  `json:"reason"`
	CorrectionBatchID *string `json:"correction_batch_id"`
}

func (r *Revision) view(paymentID string) revisionView {
	return revisionView{PaymentID: paymentID, Revision: r.Revision, Amount: r.Amount,
		EffectiveAt: r.EffectiveAt, RecordedAt: r.RecordedAt, Reason: r.Reason, CorrectionBatchID: r.CorrectionBatchID}
}

func (r *Revision) parseTimes() bool {
	var ok1, ok2 bool
	r.effective, ok1 = parseInstant(r.EffectiveAt)
	r.recorded, ok2 = parseInstant(r.RecordedAt)
	return ok1 && ok2
}

func (p *Payment) latestRevision() *Revision {
	return p.Revisions[len(p.Revisions)-1]
}

// revisionKnownAt is the latest revision recorded at or before knownAt, or nil.
func (p *Payment) revisionKnownAt(knownAt time.Time) *Revision {
	for i := len(p.Revisions) - 1; i >= 0; i-- {
		if !p.Revisions[i].recorded.After(knownAt) {
			return p.Revisions[i]
		}
	}
	return nil
}

// immutable reports whether no correction may ever change the payment: captures and refunds.
func (p *Payment) immutable() bool {
	return p.AuthorizationID != nil || p.RefundOf != nil
}

// deltaFor is the signed effect of amount on the user's balance.
func (p *Payment) deltaFor(userID string, amount int64) int64 {
	switch userID {
	case p.FromUserID:
		return -amount
	case p.ToUserID:
		return amount
	}
	return 0
}

// Request asks the payer to send money to the requester.
type Request struct {
	ID              string  `json:"id"`
	Seq             int64   `json:"seq"`
	RequesterID     string  `json:"requester_id"`
	RequesterHandle string  `json:"requester_handle"`
	PayerID         string  `json:"payer_id"`
	PayerHandle     string  `json:"payer_handle"`
	Amount          int64   `json:"amount"`
	Note            string  `json:"note"`
	Status          string  `json:"status"`
	PaymentID       *string `json:"payment_id"`
	CreatedAt       string  `json:"created_at"`
}

// IdempotencyRecord remembers a successful idempotent write for replay.
type IdempotencyRecord struct {
	UserID   string          `json:"user_id"`
	Path     string          `json:"path"`
	Key      string          `json:"key"`
	Body     string          `json:"body"`
	Response json.RawMessage `json:"response"`
}

func (r *IdempotencyRecord) scope() string {
	return idempotencyScope(r.UserID, r.Path, r.Key)
}

func idempotencyScope(userID, path, key string) string {
	return userID + "\x00" + path + "\x00" + key
}

type paymentView struct {
	PaymentID       string  `json:"payment_id"`
	FromUserID      string  `json:"from_user_id"`
	FromHandle      string  `json:"from_handle"`
	ToUserID        string  `json:"to_user_id"`
	ToHandle        string  `json:"to_handle"`
	Amount          int64   `json:"amount"`
	Currency        string  `json:"currency"`
	Note            string  `json:"note"`
	Visibility      string  `json:"visibility"`
	RequestID       *string `json:"request_id"`
	SettlementID    *string `json:"settlement_id"`
	AuthorizationID *string `json:"authorization_id"`
	RefundOf        *string `json:"refund_of"`
	CreatedAt       string  `json:"created_at"`
}

type requestView struct {
	RequestID       string  `json:"request_id"`
	RequesterID     string  `json:"requester_id"`
	RequesterHandle string  `json:"requester_handle"`
	PayerID         string  `json:"payer_id"`
	PayerHandle     string  `json:"payer_handle"`
	Amount          int64   `json:"amount"`
	Currency        string  `json:"currency"`
	Note            string  `json:"note"`
	Status          string  `json:"status"`
	PaymentID       *string `json:"payment_id"`
	CreatedAt       string  `json:"created_at"`
}

func (p *Payment) view(currency string) paymentView {
	return paymentView{
		PaymentID: p.ID, FromUserID: p.FromUserID, FromHandle: p.FromHandle,
		ToUserID: p.ToUserID, ToHandle: p.ToHandle, Amount: p.Amount, Currency: currency,
		Note: p.Note, Visibility: p.Visibility, RequestID: p.RequestID,
		SettlementID: p.SettlementID, AuthorizationID: p.AuthorizationID,
		RefundOf: p.RefundOf, CreatedAt: p.CreatedAt,
	}
}

func (r *Request) view(currency string) requestView {
	return requestView{
		RequestID: r.ID, RequesterID: r.RequesterID, RequesterHandle: r.RequesterHandle,
		PayerID: r.PayerID, PayerHandle: r.PayerHandle, Amount: r.Amount, Currency: currency,
		Note: r.Note, Status: r.Status, PaymentID: r.PaymentID, CreatedAt: r.CreatedAt,
	}
}

func newID(prefix string) string {
	b := make([]byte, 10)
	rand.Read(b)
	return prefix + hex.EncodeToString(b)
}

func newToken() string {
	b := make([]byte, 32)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// formatTime renders an instant with millisecond precision. Instants are truncated to
// milliseconds before use (see currentTime), so the rendered value is the exact instant.
func formatTime(t time.Time) string {
	return t.UTC().Format("2006-01-02T15:04:05.000-07:00")
}

// currentTime is now, at the precision timestamps are stored and rendered with.
func currentTime() time.Time {
	return time.Now().Truncate(time.Millisecond)
}

// parseInstant accepts only an RFC 3339 date-time with an explicit offset.
func parseInstant(s string) (time.Time, bool) {
	t, err := time.Parse(time.RFC3339Nano, s)
	return t, err == nil
}

func validAuthorizationStatus(s string) bool {
	return s == authOpen || s == authCaptured || s == authVoided || s == authExpired
}

func validRequestStatus(s string) bool {
	return s == statusPending || s == statusPaid || s == statusDeclined || s == statusCancelled
}

// Authorization reserves money from the payer for the receiver to capture later.
// Status is the stored status; an open authorization past its deadline reads as expired.
type Authorization struct {
	ID             string   `json:"id"`
	Seq            int64    `json:"seq"`
	FromUserID     string   `json:"from_user_id"`
	FromHandle     string   `json:"from_handle"`
	ToUserID       string   `json:"to_user_id"`
	ToHandle       string   `json:"to_handle"`
	Amount         int64    `json:"amount"`
	CapturedAmount int64    `json:"captured_amount"`
	Note           string   `json:"note"`
	Visibility     string   `json:"visibility"`
	Status         string   `json:"status"`
	ExpiresAt      string   `json:"expires_at"`
	PaymentIDs     []string `json:"payment_ids"`
	CreatedAt      string   `json:"created_at"`
	// ClosedAt is when a final capture or void closed the hold; clock expiry is derived.
	ClosedAt string `json:"closed_at"`

	created time.Time
	expires time.Time
	closed  *time.Time
}

func (a *Authorization) expiredAt(now time.Time) bool {
	return !now.Before(a.expires)
}

// statusAt is the status as seen at the given time.
func (a *Authorization) statusAt(now time.Time) string {
	if a.Status == authOpen && a.expiredAt(now) {
		return authExpired
	}
	return a.Status
}

// heldAt is the amount still reserved from the payer.
func (a *Authorization) heldAt(now time.Time) int64 {
	if a.statusAt(now) != authOpen {
		return 0
	}
	return a.Amount - a.CapturedAmount
}

type authorizationView struct {
	AuthorizationID string   `json:"authorization_id"`
	FromUserID      string   `json:"from_user_id"`
	FromHandle      string   `json:"from_handle"`
	ToUserID        string   `json:"to_user_id"`
	ToHandle        string   `json:"to_handle"`
	Amount          int64    `json:"amount"`
	CapturedAmount  int64    `json:"captured_amount"`
	RemainingAmount int64    `json:"remaining_amount"`
	Currency        string   `json:"currency"`
	Note            string   `json:"note"`
	Visibility      string   `json:"visibility"`
	Status          string   `json:"status"`
	ExpiresAt       string   `json:"expires_at"`
	PaymentID       *string  `json:"payment_id"`
	PaymentIDs      []string `json:"payment_ids"`
	CreatedAt       string   `json:"created_at"`
	ClosedAt        *string  `json:"closed_at"`
}

// closedAtText is when the hold closed as seen at now: its close event, or its deadline once expired.
func (a *Authorization) closedAtText(now time.Time) *string {
	switch {
	case a.Status != authOpen:
		return &a.ClosedAt
	case a.expiredAt(now):
		return &a.ExpiresAt
	}
	return nil
}

func (a *Authorization) view(currency string, now time.Time) authorizationView {
	var latest *string
	if n := len(a.PaymentIDs); n > 0 {
		latest = &a.PaymentIDs[n-1]
	}
	paymentIDs := a.PaymentIDs
	if paymentIDs == nil {
		paymentIDs = []string{}
	}
	return authorizationView{
		AuthorizationID: a.ID, FromUserID: a.FromUserID, FromHandle: a.FromHandle,
		ToUserID: a.ToUserID, ToHandle: a.ToHandle, Amount: a.Amount,
		CapturedAmount: a.CapturedAmount, RemainingAmount: a.heldAt(now), Currency: currency,
		Note: a.Note, Visibility: a.Visibility, Status: a.statusAt(now), ExpiresAt: a.ExpiresAt,
		PaymentID: latest, PaymentIDs: paymentIDs, CreatedAt: a.CreatedAt, ClosedAt: a.closedAtText(now),
	}
}
