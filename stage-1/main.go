// Command pocketful serves the Pocketful payments API.
package main

import (
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	server := &http.Server{
		Addr:              "0.0.0.0:" + port,
		Handler:           NewServer(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("pocketful listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}
