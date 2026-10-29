// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestSummaryHTTPContractsCannotExposePrivateData(t *testing.T) {
	missing := `{"detail":[{"type":"missing","loc":["query","character_id"],"msg":"Field required","input":null}]}`
	for _, c := range []struct {
		name, path, body string
		status, expected int
	}{
		{"empty list", "/api/conversations?character_id=mano", "[\"meta\",2,\"sc\",\"list\",\"u\",\"raw-fields-v1\"]\n[\"end\"]\n", 200, 200},
		{"empty latest", "/api/conversations/latest", "[\"meta\",2,\"sc\",\"latest\",null,\"raw-fields-v1\"]\n[\"end\"]\n", 200, 200},
		{"missing parameter", "/api/conversations", missing, 422, 422},
		{"private extra in validation", "/api/conversations", strings.Replace(missing, `"type":`, `"raw_preview":"secret","type":`, 1), 422, 502},
		{"truncated list", "/api/conversations?character_id=mano", "[\"meta\",2,\"sc\",\"list\",\"u\",\"raw-fields-v1\"]\n", 200, 502},
		{"trailing private record", "/api/conversations/latest", "[\"meta\",2,\"sc\",\"latest\",null,\"raw-fields-v1\"]\n[\"end\"]\n[\"raw_preview\",42]\n", 200, 502},
	} {
		func() {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				expectedPath := "/api/_core/conversation-summaries"
				if strings.Contains(c.path, "/latest") {
					expectedPath += "/latest"
				}
				if r.URL.Path != expectedPath || r.Method != "GET" || r.Header.Get("Authorization") != "Bearer core" {
					t.Errorf("%s: identity/path changed", c.name)
				}
				w.Header().Set("Content-Type", "application/x-ndjson")
				w.Header().Set(displayHeader, displayContract)
				w.Header().Add("Set-Cookie", "a=1; HttpOnly")
				w.Header().Add("Set-Cookie", "b=2; HttpOnly")
				w.WriteHeader(c.status)
				w.Write([]byte(c.body))
			}))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			response := httptest.NewRecorder()
			NewSummaryHandler(target, http.DefaultTransport, "core").ServeHTTP(response, httptest.NewRequest("GET", c.path, nil))
			if response.Code != c.expected || len(response.Header().Values("Set-Cookie")) != 2 || strings.Contains(response.Body.String(), "raw_preview") {
				t.Fatalf("%s: %d %s", c.name, response.Code, response.Body.String())
			}
			if c.expected == 200 && response.Body.String() != "[]" {
				t.Fatalf("%s: wrong array", c.name)
			}
		}()
	}
}

func TestSummaryCancellationClosesUpstreamWithoutPublishingArray(t *testing.T) {
	entered, observed, cleanup := make(chan struct{}), make(chan struct{}), make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Header().Set(displayHeader, displayContract)
		w.Write([]byte("[\"meta\",2,\"sc\",\"latest\",null,\"raw-fields-v1\"]\n"))
		w.(http.Flusher).Flush()
		close(entered)
		select {
		case <-r.Context().Done():
			close(observed)
		case <-cleanup:
		}
	}))
	target, _ := url.Parse(upstream.URL)
	downstream := httptest.NewServer(NewSummaryHandler(target, http.DefaultTransport, "core"))
	defer func() { close(cleanup); downstream.Close(); upstream.Close() }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, "GET", downstream.URL+"/api/conversations/latest", nil)
	done := make(chan struct{})
	go func() {
		response, _ := http.DefaultClient.Do(request)
		if response != nil {
			response.Body.Close()
		}
		close(done)
	}()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("private summary not entered")
	}
	started := time.Now()
	cancel()
	select {
	case <-observed:
		t.Logf("summary cancel %v", time.Since(started))
	case <-time.After(2 * time.Second):
		t.Fatal("private summary not cancelled")
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("summary client stuck")
	}
}
