package main

import (
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"strings"
)

const passwordIterations = 20000

// hashPassword derives a salted PBKDF2-SHA256 hash in the form "salt$hash" (hex).
func hashPassword(password string) string {
	salt := make([]byte, 16)
	rand.Read(salt)
	return hex.EncodeToString(salt) + "$" + hex.EncodeToString(derive(password, salt))
}

func checkPassword(password, stored string) bool {
	saltHex, hashHex, ok := strings.Cut(stored, "$")
	if !ok {
		return false
	}
	salt, err1 := hex.DecodeString(saltHex)
	want, err2 := hex.DecodeString(hashHex)
	if err1 != nil || err2 != nil {
		return false
	}
	return subtle.ConstantTimeCompare(derive(password, salt), want) == 1
}

func derive(password string, salt []byte) []byte {
	key, err := pbkdf2.Key(sha256.New, password, salt, passwordIterations, 32)
	if err != nil {
		panic(err)
	}
	return key
}
