package main

import (
	"embed"
	"io/fs"
	"net/http"
	"strings"
)

// webFiles holds the browser client: index.html and app.js (builder) and static/ (designer).
//
//go:embed web
var webFiles embed.FS

func mustRead(name string) []byte {
	data, err := webFiles.ReadFile(name)
	if err != nil {
		panic(err)
	}
	return data
}

var (
	indexPage = mustRead("web/index.html")
	appScript = mustRead("web/app.js")
)

func staticHandler() http.Handler {
	static, err := fs.Sub(webFiles, "web/static")
	if err != nil {
		panic(err)
	}
	return http.StripPrefix("/static/", http.FileServerFS(static))
}

func servePage(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Write(indexPage)
}

func serveScript(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Write(appScript)
}

func wantsHTML(r *http.Request) bool {
	return strings.Contains(r.Header.Get("Accept"), "text/html")
}

// pageOrAPI serves the browser page for Accept: text/html and the JSON API otherwise.
func pageOrAPI(api http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if wantsHTML(r) {
			servePage(w, r)
			return
		}
		api(w, r)
	}
}
