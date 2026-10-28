// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"context"
	"time"

	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

func TestPrivateReadMustBeCompleteBeforePublicSuccess(t *testing.T) {
	for _, c := range []struct {
		name, tail string
		expected   int
	}{
		{"empty complete", "[\"end\"]\n", 200},
		{"truncated", "", 502},
		{"trailing private data", "[\"end\"]\n[\"secret\",42]\n", 502},
	} {
		func() {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "GET" || r.URL.Path != "/api/_core/conversations/c/messages" || r.Header.Get("Authorization") != "Bearer core" || r.Header.Get("Idempotency-Key") != "" {
					t.Errorf("%s: private request identity changed", c.name)
				}
				w.Header().Set("Content-Type", "application/x-ndjson")
				w.Header().Set("X-Core-History-Comparison", "python-us-v1")

				w.Header().Add("Set-Cookie", "a=1; HttpOnly")
				w.Header().Add("Set-Cookie", "b=2; HttpOnly")
				w.Write([]byte("[\"meta\",1,\"sc\",\"c\"]\n" + c.tail))
			}))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			response := httptest.NewRecorder()
			NewHandler(target, http.DefaultTransport, "core").ServeHTTP(response, httptest.NewRequest("GET", "/api/conversations/c/messages", nil))
			if response.Code != c.expected || len(response.Header().Values("Set-Cookie")) != 2 {
				t.Fatalf("%s: %d %s", c.name, response.Code, response.Body.String())
			}
			if c.expected == 200 && response.Body.String() != "[]" {
				t.Fatalf("%s: array changed", c.name)
			}
			if c.expected == 502 && strings.Contains(response.Body.String(), "secret") {
				t.Fatalf("%s: private output", c.name)
			}
		}()
	}
}

func TestNoAutomaticSecondReadAfterUpstreamDisconnect(t *testing.T) {
	var count atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if count.Add(1) == 2 {
			connection, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			connection.Close()
			return
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Header().Set("X-Core-History-Comparison", "python-us-v1")

		w.Write([]byte("[\"meta\",1,\"sc\",\"c\"]\n[\"end\"]\n"))
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	handler := NewHandler(target, http.DefaultTransport, "core")
	for index, expected := range []int{200, 502} {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest("GET", "/api/conversations/c/messages", nil))
		if response.Code != expected {
			t.Fatalf("request %d: %d, upstream reads=%d", index, response.Code, count.Load())
		}
	}
	if count.Load() != 2 {
		t.Fatalf("read watermark retried: %d", count.Load())
	}
}

func TestCancelClosesPrivateStreamWithoutPublishingPartialArray(t *testing.T) {
	entered, observed, cleanup := make(chan struct{}), make(chan struct{}), make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Header().Set("X-Core-History-Comparison", "python-us-v1")

		w.Write([]byte("[\"meta\",1,\"sc\",\"c\"]\n"))
		w.(http.Flusher).Flush()
		close(entered)
		select {
		case <-r.Context().Done():
			close(observed)
		case <-cleanup:
		}
	}))
	target, _ := url.Parse(upstream.URL)
	downstream := httptest.NewServer(NewHandler(target, http.DefaultTransport, "core"))
	defer func() { close(cleanup); downstream.Close(); upstream.Close() }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, "GET", downstream.URL+"/api/conversations/c/messages", nil)
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
		t.Fatal("private stream not entered")
	}
	started := time.Now()
	cancel()
	select {
	case <-observed:
		t.Logf("timeline cancel %v", time.Since(started))
	case <-time.After(2 * time.Second):
		t.Fatal("private request not cancelled")
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("downstream stuck")
	}
}

func TestHistoryComparisonCapabilityFailsClosed(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte("[\"meta\",1,\"sc\",\"c\"]\n[\"end\"]\n"))
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	response := httptest.NewRecorder()
	NewHandler(target, http.DefaultTransport, "core").ServeHTTP(response, httptest.NewRequest("GET", "/api/conversations/c/messages", nil))
	if response.Code != 502 {
		t.Fatal("unmatched comparison capability silently accepted", response.Code)
	}
}
