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
				w.Header().Set(displayHeader, displayContract)
				w.Header().Set("X-Core-History-Comparison", "python-us-v1")

				w.Header().Add("Set-Cookie", "a=1; HttpOnly")
				w.Header().Add("Set-Cookie", "b=2; HttpOnly")
				w.Write([]byte("[\"meta\",2,\"sc\",\"c\",\"raw-fields-v1\"]\n" + c.tail))
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
		w.Header().Set(displayHeader, displayContract)
		w.Header().Set("X-Core-History-Comparison", "python-us-v1")

		w.Write([]byte("[\"meta\",2,\"sc\",\"c\",\"raw-fields-v1\"]\n[\"end\"]\n"))
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
		w.Header().Set(displayHeader, displayContract)
		w.Header().Set("X-Core-History-Comparison", "python-us-v1")

		w.Write([]byte("[\"meta\",2,\"sc\",\"c\",\"raw-fields-v1\"]\n"))
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
		w.Header().Set(displayHeader, displayContract)
		w.Write([]byte("[\"meta\",2,\"sc\",\"c\",\"raw-fields-v1\"]\n[\"end\"]\n"))
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	response := httptest.NewRecorder()
	NewHandler(target, http.DefaultTransport, "core").ServeHTTP(response, httptest.NewRequest("GET", "/api/conversations/c/messages", nil))
	if response.Code != 502 {
		t.Fatal("unmatched comparison capability silently accepted", response.Code)
	}
}

func TestRawDisplayNegotiationFailsClosed(t *testing.T) {
	for _, c := range []struct {
		name   string
		ack    []string
		meta   string
		status int
	}{
		{"old Python", nil, `["meta",1,"sc","c"]`, 200},
		{"stripped ack", nil, `["meta",2,"sc","c","raw-fields-v1"]`, 200},
		{"duplicate ack", []string{"raw-fields-v1", "raw-fields-v1"}, `["meta",2,"sc","c","raw-fields-v1"]`, 200},
		{"combined ack", []string{"raw-fields-v1, raw-fields-v1"}, `["meta",2,"sc","c","raw-fields-v1"]`, 200},
		{"unknown ack", []string{"future"}, `["meta",2,"sc","c","raw-fields-v1"]`, 200},
		{"old meta", []string{"raw-fields-v1"}, `["meta",1,"sc","c"]`, 200},
		{"unknown contract", []string{"raw-fields-v1"}, `["meta",2,"sc","c","future"]`, 200},
		{"protocol refusal", nil, `{"detail":"unsupported display text contract"}`, 409},
	} {
		t.Run(c.name, func(t *testing.T) {
			reads := 0
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				reads++
				if r.Header.Values(displayHeader)[0] != displayContract || len(r.Header.Values(displayHeader)) != 1 {
					t.Error("client chose private capability")
				}
				w.Header().Set("Content-Type", "application/x-ndjson")
				w.Header().Set("X-Core-History-Comparison", "python-us-v1")
				for _, value := range c.ack {
					w.Header().Add(displayHeader, value)
				}
				w.WriteHeader(c.status)
				w.Write([]byte(c.meta + "\n[\"end\"]\n"))
			}))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			response := httptest.NewRecorder()
			request := httptest.NewRequest("GET", "/api/conversations/c/messages", nil)
			request.Header.Add(displayHeader, "client-forged")
			NewHandler(target, http.DefaultTransport, "core").ServeHTTP(response, request)
			if response.Code != 502 || reads != 1 || response.Header().Get(displayHeader) != "" || response.Body.String() != "{\"detail\":\"SC history unavailable\"}\n" {
				t.Fatal(response.Code, reads, response.Body.String())
			}
		})
	}
}

func TestRawUnicodeIsValidatedBeforeJSONReplacement(t *testing.T) {
	for _, c := range []struct {
		name, field string
		status      int
	}{
		{"paired surrogate", `"\ud83d\ude42"`, 200}, {"literal escaped surrogate", `"\\ud800"`, 200},
		{"unpaired high", `"\ud800"`, 502}, {"unpaired low", `"\udc00"`, 502}, {"high then nonlow", `"\ud800\u0061"`, 502},
		{"invalid UTF8", "\"" + string([]byte{0xff}) + "\"", 502},
	} {
		t.Run(c.name, func(t *testing.T) {
			raw := `["meta",2,"sc","c","raw-fields-v1"]` + "\n" + `["message",{"id":1,"role":"character","text":null,"ja":` + c.field + `,"zh":"","created_at":"2026-10-28T00:00:00Z","segments":[],"affinity":null,"photo":null,"comparison_order":{"micros":1,"aware":true}}]` + "\n[\"end\"]\n"
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/x-ndjson")
				w.Header().Set(displayHeader, displayContract)
				w.Header().Set("X-Core-History-Comparison", "python-us-v1")
				w.Write([]byte(raw))
			}))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			response := httptest.NewRecorder()
			NewHandler(target, http.DefaultTransport, "core").ServeHTTP(response, httptest.NewRequest("GET", "/api/conversations/c/messages", nil))
			if response.Code != c.status {
				t.Fatal(response.Code, response.Body.String())
			}
		})
	}
}
