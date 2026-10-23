package main

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSeed(t *testing.T) {
	server := httptest.NewServer(http.NewServeMux())
	defer server.Close()
	response, err := http.Post(server.URL, "application/json", bytes.NewBufferString("{}"))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
}
