package main

import (
	"net/http"
	"regexp"
	"strings"
	"unicode/utf8"
)

var emailPattern = regexp.MustCompile(`^[^@\s]+@[^@\s]+$`)

const minPasswordLength = 8

// deriveHandle builds a handle from an email's local part (§4).
func deriveHandle(email string) string {
	local, _, _ := strings.Cut(email, "@")
	var b strings.Builder
	for _, c := range strings.ToLower(local) {
		if (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' {
			b.WriteRune(c)
		} else {
			b.WriteByte('_')
		}
	}
	handle := b.String()
	if len(handle) > 20 {
		handle = handle[:20]
	}
	return handle
}

type session struct {
	UserID      string `json:"user_id"`
	DisplayName string `json:"display_name"`
	Token       string `json:"token"`
}

// checkSignupConflicts reports an email or derived handle that is already in use.
func (st *State) checkSignupConflicts(email, handle string) *apiError {
	if st.usersByEmail[emailKey(email)] != nil {
		return &apiError{409, "email_taken", "that email is already registered"}
	}
	if st.usersByHandle[handle] != nil {
		return &apiError{409, "handle_taken", "the handle derived from that email is taken"}
	}
	return nil
}

func (srv *Server) signup(w http.ResponseWriter, r *http.Request) {
	body, apiErr := readObject(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	email, apiErr := requiredString(body, "email")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	password, apiErr := requiredString(body, "password")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	displayName, apiErr := requiredString(body, "display_name")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	if !emailPattern.MatchString(email) {
		writeError(w, errValidation("email must have the form local@domain"))
		return
	}
	if utf8.RuneCountInString(password) < minPasswordLength {
		writeError(w, errValidation("password must be at least 8 characters"))
		return
	}
	handle := deriveHandle(email)

	srv.store.mu.Lock()
	apiErr = srv.store.state.checkSignupConflicts(email, handle)
	srv.store.mu.Unlock()
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}

	passwordHash := hashPassword(password)

	srv.store.mu.Lock()
	defer srv.store.mu.Unlock()
	st := srv.store.state
	if apiErr := st.checkSignupConflicts(email, handle); apiErr != nil {
		writeError(w, apiErr)
		return
	}
	user := &User{ID: newID("usr_"), Email: email, PasswordHash: passwordHash, DisplayName: displayName, Handle: handle}
	st.addUser(user)
	writeJSON(w, http.StatusCreated, session{UserID: user.ID, DisplayName: user.DisplayName, Token: st.issueToken(user.ID)})
}

func (srv *Server) login(w http.ResponseWriter, r *http.Request) {
	body, apiErr := readObject(r)
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	email, apiErr := requiredString(body, "email")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}
	password, apiErr := requiredString(body, "password")
	if apiErr != nil {
		writeError(w, apiErr)
		return
	}

	srv.store.mu.Lock()
	user := srv.store.state.usersByEmail[emailKey(email)]
	var passwordHash string
	if user != nil {
		passwordHash = user.PasswordHash
	}
	srv.store.mu.Unlock()

	if user == nil || !checkPassword(password, passwordHash) {
		writeError(w, &apiError{401, "unauthenticated", "wrong email or password"})
		return
	}

	srv.store.mu.Lock()
	defer srv.store.mu.Unlock()
	st := srv.store.state
	if st.usersByID[user.ID] != user {
		// State was replaced while the password was being checked.
		writeError(w, &apiError{401, "unauthenticated", "wrong email or password"})
		return
	}
	writeJSON(w, http.StatusOK, session{UserID: user.ID, DisplayName: user.DisplayName, Token: st.issueToken(user.ID)})
}
