package main

import (
	"log"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

type Server struct {
	store *Store
	mux   *http.ServeMux
}

func NewServer() *Server {
	srv := &Server{store: &Store{state: emptyState()}, mux: http.NewServeMux()}
	srv.mux.HandleFunc("GET /health", srv.health)
	for _, page := range []string{"/{$}", "/split", "/login", "/signup"} {
		srv.mux.HandleFunc("GET "+page, servePage)
	}
	srv.mux.HandleFunc("GET /app.js", serveScript)
	srv.mux.Handle("GET /static/", staticHandler())
	srv.mux.HandleFunc("POST /_test/reset", srv.reset)
	srv.mux.HandleFunc("GET /_test/export", srv.export)
	srv.mux.HandleFunc("POST /_test/import", srv.importState)
	srv.mux.HandleFunc("POST /auth/signup", srv.signup)
	srv.mux.HandleFunc("POST /auth/login", srv.login)
	srv.mux.HandleFunc("GET /me", srv.authenticated(srv.me))
	srv.mux.HandleFunc("GET /activity", srv.authenticated(srv.activity))
	srv.mux.HandleFunc("GET /requests", pageOrAPI(srv.authenticated(srv.listRequests)))
	srv.mux.HandleFunc("POST /requests/{id}/decline", srv.authenticated(srv.declineRequest))
	srv.mux.HandleFunc("POST /requests/{id}/cancel", srv.authenticated(srv.cancelRequest))
	srv.mux.HandleFunc("POST /payments", srv.idempotent(srv.createPayment, idempotentOptions{}))
	srv.mux.HandleFunc("POST /requests", srv.idempotent(srv.createRequest, idempotentOptions{}))
	srv.mux.HandleFunc("POST /requests/{id}/pay", srv.idempotent(srv.payRequest, idempotentOptions{allowEmptyBody: true}))
	srv.mux.HandleFunc("POST /splits", srv.idempotent(srv.createSplit, idempotentOptions{}))
	srv.mux.HandleFunc("GET /authorizations", pageOrAPI(srv.authenticated(srv.listAuthorizations)))
	srv.mux.HandleFunc("POST /authorizations", srv.idempotent(srv.createAuthorization, idempotentOptions{}))
	srv.mux.HandleFunc("POST /authorizations/{id}/capture", srv.idempotent(srv.captureAuthorization, idempotentOptions{allowEmptyBody: true}))
	srv.mux.HandleFunc("POST /authorizations/{id}/void", srv.authenticated(srv.voidAuthorization))
	srv.mux.HandleFunc("GET /statement", srv.authenticated(srv.statement))
	srv.mux.HandleFunc("GET /payments/{payment_id}/revisions", srv.authenticated(srv.paymentRevisions))
	srv.mux.HandleFunc("POST /payments/{payment_id}/corrections", srv.idempotent(srv.correctPayment, idempotentOptions{}))
	srv.mux.HandleFunc("POST /payments/{payment_id}/refunds", srv.idempotent(srv.refundPayment, idempotentOptions{}))
	srv.mux.HandleFunc("POST /correction-batches", srv.idempotent(srv.createCorrectionBatch, idempotentOptions{operatorOnly: true}))
	srv.mux.HandleFunc("POST /settlements", srv.idempotent(srv.createSettlement, idempotentOptions{operatorOnly: true}))
	return srv
}

func (srv *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	defer func() {
		if rec := recover(); rec != nil {
			log.Printf("panic serving %s %s: %v", r.Method, r.URL.Path, rec)
			writeError(w, &apiError{500, "internal_error", "internal error"})
		}
	}()
	if _, pattern := srv.mux.Handler(r); pattern == "" {
		writeError(w, errNotFound("no such endpoint"))
		return
	}
	srv.mux.ServeHTTP(w, r)
}

// bearerToken extracts the token from an "Authorization: Bearer <token>" header.
func bearerToken(r *http.Request) string {
	scheme, token, ok := strings.Cut(r.Header.Get("Authorization"), " ")
	if !ok || !strings.EqualFold(scheme, "Bearer") {
		return ""
	}
	return strings.TrimSpace(token)
}

// userFor resolves the caller. The store lock must be held.
func (st *State) userFor(r *http.Request) *User {
	token := bearerToken(r)
	if token == "" {
		return nil
	}
	return st.usersByID[st.Tokens[token]]
}

type authedHandler func(w http.ResponseWriter, r *http.Request, st *State, user *User)

// authenticated runs h under the store lock with the authenticated caller.
func (srv *Server) authenticated(h authedHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		srv.store.mu.Lock()
		defer srv.store.mu.Unlock()
		st := srv.store.state
		user := st.userFor(r)
		if user == nil {
			writeError(w, errUnauthenticated())
			return
		}
		h(w, r, st, user)
	}
}

// writeOperation performs an idempotent write and returns the 201 response body.
type writeOperation func(r *http.Request, st *State, user *User, body map[string]any, now time.Time) (any, *apiError)

type idempotentOptions struct {
	allowEmptyBody bool
	operatorOnly   bool
}

// idempotent wraps a write path with authentication and Idempotency-Key handling (§7).
// The whole operation runs under the store lock, so concurrent identical requests
// resolve to one 201 and replays of it.
func (srv *Server) idempotent(op writeOperation, opts idempotentOptions) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		data, readErr := readBody(r)

		srv.store.mu.Lock()
		defer srv.store.mu.Unlock()
		st := srv.store.state

		user := st.userFor(r)
		if user == nil {
			writeError(w, errUnauthenticated())
			return
		}
		if opts.operatorOnly && !st.operators[user.ID] {
			writeError(w, errForbidden("only settlement operators may do this"))
			return
		}
		key := r.Header.Get("Idempotency-Key")
		if key == "" {
			writeError(w, &apiError{400, "missing_idempotency_key", "Idempotency-Key header is required"})
			return
		}
		if utf8.RuneCountInString(key) > 255 {
			writeError(w, errValidation("Idempotency-Key must be 1 to 255 characters"))
			return
		}
		if readErr != nil {
			writeError(w, readErr)
			return
		}
		body, apiErr := parseObject(data, opts.allowEmptyBody)
		if apiErr != nil {
			writeError(w, apiErr)
			return
		}

		canonical := canonicalJSON(body)
		scope := idempotencyScope(user.ID, r.URL.Path, key)
		if rec := st.idempotency[scope]; rec != nil {
			if rec.Body != canonical {
				writeError(w, &apiError{409, "idempotency_key_reuse", "this key was already used with a different body"})
				return
			}
			writeRaw(w, http.StatusOK, rec.Response)
			return
		}

		result, apiErr := op(r, st, user, body, currentTime())
		if apiErr != nil {
			writeError(w, apiErr)
			return
		}
		response := encodeJSON(result)
		st.remember(&IdempotencyRecord{UserID: user.ID, Path: r.URL.Path, Key: key, Body: canonical, Response: response})
		writeRaw(w, http.StatusCreated, response)
	}
}

func (srv *Server) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
