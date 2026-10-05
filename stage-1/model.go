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
}

// Payment is one completed money movement between two wallets.
type Payment struct {
	ID           string  `json:"id"`
	Seq          int64   `json:"seq"`
	FromUserID   string  `json:"from_user_id"`
	FromHandle   string  `json:"from_handle"`
	ToUserID     string  `json:"to_user_id"`
	ToHandle     string  `json:"to_handle"`
	Amount       int64   `json:"amount"`
	Note         string  `json:"note"`
	Visibility   string  `json:"visibility"`
	RequestID    *string `json:"request_id"`
	SettlementID *string `json:"settlement_id"`
	CreatedAt    string  `json:"created_at"`
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
	PaymentID    string  `json:"payment_id"`
	FromUserID   string  `json:"from_user_id"`
	FromHandle   string  `json:"from_handle"`
	ToUserID     string  `json:"to_user_id"`
	ToHandle     string  `json:"to_handle"`
	Amount       int64   `json:"amount"`
	Currency     string  `json:"currency"`
	Note         string  `json:"note"`
	Visibility   string  `json:"visibility"`
	RequestID    *string `json:"request_id"`
	SettlementID *string `json:"settlement_id"`
	CreatedAt    string  `json:"created_at"`
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
		SettlementID: p.SettlementID, CreatedAt: p.CreatedAt,
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

func formatTime(t time.Time) string {
	return t.UTC().Format("2006-01-02T15:04:05-07:00")
}

func validRequestStatus(s string) bool {
	return s == statusPending || s == statusPaid || s == statusDeclined || s == statusCancelled
}
