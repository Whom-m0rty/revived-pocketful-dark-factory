package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"
)

const maxBodyBytes = 1 << 20

// apiError is an error response with a status and a machine-readable code.
type apiError struct {
	status  int
	code    string
	message string
}

func errMalformed(msg string) *apiError  { return &apiError{400, "malformed_request", msg} }
func errValidation(msg string) *apiError { return &apiError{422, "validation_failed", msg} }
func errNotFound(msg string) *apiError   { return &apiError{404, "not_found", msg} }
func errForbidden(msg string) *apiError  { return &apiError{403, "forbidden", msg} }
func errUnauthenticated() *apiError {
	return &apiError{401, "unauthenticated", "missing, malformed or unknown bearer token"}
}
func errInsufficientFunds() *apiError {
	return &apiError{409, "insufficient_funds", "balance is too low for this amount"}
}
func errNotPending() *apiError {
	return &apiError{409, "request_not_pending", "the request is no longer pending"}
}

func encodeJSON(v any) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		panic(err)
	}
	return bytes.TrimRight(buf.Bytes(), "\n")
}

func writeRaw(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	w.Write(body)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	writeRaw(w, status, encodeJSON(v))
}

func writeError(w http.ResponseWriter, e *apiError) {
	writeJSON(w, e.status, map[string]any{"error": map[string]string{"code": e.code, "message": e.message}})
}

func writeNoContent(w http.ResponseWriter) {
	w.WriteHeader(http.StatusNoContent)
}

// readBody reads the raw request body, up to maxBodyBytes.
func readBody(r *http.Request) ([]byte, *apiError) {
	data, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil || len(data) > maxBodyBytes {
		return nil, errMalformed("request body could not be read")
	}
	return data, nil
}

// decodeJSON parses a single JSON value with numbers kept exact.
func decodeJSON(data []byte) (any, *apiError) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, errMalformed("request body is not valid JSON")
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errMalformed("request body has trailing data")
	}
	return v, nil
}

// parseObject requires the body to be a JSON object.
// An empty body is accepted as {} when allowEmpty is set.
func parseObject(data []byte, allowEmpty bool) (map[string]any, *apiError) {
	if allowEmpty && len(bytes.TrimSpace(data)) == 0 {
		return map[string]any{}, nil
	}
	v, apiErr := decodeJSON(data)
	if apiErr != nil {
		return nil, apiErr
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return nil, errMalformed("request body must be a JSON object")
	}
	return obj, nil
}

// readObject reads the body and requires it to be a JSON object.
func readObject(r *http.Request) (map[string]any, *apiError) {
	data, apiErr := readBody(r)
	if apiErr != nil {
		return nil, apiErr
	}
	return parseObject(data, false)
}

// canonicalJSON renders a parsed value so that equal JSON values give equal strings:
// object keys are sorted and numbers with the same value are written the same way.
func canonicalJSON(v any) string {
	return string(encodeJSON(normalizeNumbers(v)))
}

func normalizeNumbers(v any) any {
	switch t := v.(type) {
	case json.Number:
		f, ok := parseNumber(t)
		if !ok {
			return t
		}
		if f.IsInt() {
			i, _ := f.Int(nil)
			return json.Number(i.String())
		}
		return json.Number(f.Text('g', -1))
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, item := range t {
			out[k] = normalizeNumbers(item)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, item := range t {
			out[i] = normalizeNumbers(item)
		}
		return out
	}
	return v
}

func parseNumber(n json.Number) (*big.Float, bool) {
	f, _, err := big.ParseFloat(string(n), 10, 256, big.ToNearestEven)
	return f, err == nil
}

// integralValue returns the exact integer value of a JSON number, if it has one
// and it fits in an int64.
func integralValue(v any) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, ok := parseNumber(n)
	if !ok || !f.IsInt() {
		return 0, false
	}
	i, acc := f.Int64()
	return i, acc == big.Exact
}

// requiredString reads a required string field: missing is 422, wrong type is 400.
func requiredString(obj map[string]any, name string) (string, *apiError) {
	v, present := obj[name]
	if !present {
		return "", errValidation(name + " is required")
	}
	s, ok := v.(string)
	if !ok {
		return "", errMalformed(name + " must be a string")
	}
	return s, nil
}

// amountField reads a payment amount: an integral number from 1 to maxAmount.
func amountField(obj map[string]any, name string) (int64, *apiError) {
	v, present := obj[name]
	if !present {
		return 0, errValidation(name + " is required")
	}
	amount, ok := integralValue(v)
	if !ok || amount < 1 || amount > maxAmount {
		return 0, errValidation(name + " must be an integer from 1 to 1000000000")
	}
	return amount, nil
}

// noteField reads the optional note, defaulting to "".
func noteField(obj map[string]any) (string, *apiError) {
	v, present := obj["note"]
	if !present {
		return "", nil
	}
	note, ok := v.(string)
	if !ok {
		return "", errValidation("note must be a string")
	}
	if utf8.RuneCountInString(note) > maxNoteLength {
		return "", errValidation("note must be at most 200 characters")
	}
	return note, nil
}

// visibilityField reads the optional visibility, defaulting to "public".
func visibilityField(obj map[string]any) (string, *apiError) {
	v, present := obj["visibility"]
	if !present {
		return "public", nil
	}
	s, _ := v.(string)
	if s != "public" && s != "private" {
		return "", errValidation("visibility must be public or private")
	}
	return s, nil
}

// handleValue checks a handle given as a string field value.
func handleValue(name, handle string) *apiError {
	if !handlePattern.MatchString(handle) {
		return errValidation(name + " is not a valid handle")
	}
	return nil
}

// queryInt reads a decimal-digit query parameter with a lower and optional upper bound.
func queryInt(r *http.Request, name string, def, min int64, max int64) (int64, *apiError) {
	values, present := r.URL.Query()[name]
	if !present || len(values) == 0 {
		return def, nil
	}
	raw := values[0]
	if raw == "" || strings.Trim(raw, "0123456789") != "" {
		return 0, errValidation(name + " must be written as decimal digits")
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		// Too many digits for int64: only acceptable when there is no upper bound.
		if max > 0 {
			return 0, errValidation(name + " is out of range")
		}
		return 1<<62 - 1, nil
	}
	if n < min || (max > 0 && n > max) {
		return 0, errValidation(name + " is out of range")
	}
	return n, nil
}

func pagination(r *http.Request) (limit, offset int64, apiErr *apiError) {
	if limit, apiErr = queryInt(r, "limit", 50, 1, 200); apiErr != nil {
		return
	}
	offset, apiErr = queryInt(r, "offset", 0, 0, 0)
	return
}

func paginate[T any](items []T, limit, offset int64) ([]T, bool) {
	total := int64(len(items))
	if offset >= total {
		return []T{}, false
	}
	end := offset + limit
	if end >= total {
		return items[offset:], false
	}
	return items[offset:end], true
}
