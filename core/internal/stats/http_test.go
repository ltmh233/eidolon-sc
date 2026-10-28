// SPDX-License-Identifier: AGPL-3.0-or-later
package stats

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

const emptyStream = `["meta",1,"sc","all","2026-10-28T12:00:00+09:00","2026-10-01T12:00:00+09:00",false,null]
["character","mano",null]
["end"]
`

func TestPrivateMetadataProducesPublicStatsAndPreservesCookies(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/_core/stats" || r.URL.Query().Get("range") != "30d" || r.Header.Get("X-Timezone") != "America/New_York" || r.Header.Get("Cookie") != "session=synthetic" || r.Header.Get("Authorization") != "Bearer core" {
			t.Error("private request lost identity or parameters")
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Header().Add("Set-Cookie", "a=one; HttpOnly")
		w.Header().Add("Set-Cookie", "b=two; HttpOnly")
		w.Write([]byte(strings.ReplaceAll(emptyStream, `"all"`, `"30d"`)))
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	request := httptest.NewRequest("GET", "/api/stats?range=30d", nil)
	request.Header.Set("X-Timezone", "America/New_York")
	request.Header.Set("Cookie", "session=synthetic")
	request.Header.Set("Authorization", "Bearer client")
	result := httptest.NewRecorder()
	NewHandler(target, http.DefaultTransport, "core").ServeHTTP(result, request)
	var body map[string]any
	json.Unmarshal(result.Body.Bytes(), &body)
	if result.Code != 200 || body["days_known"] != float64(28) || len(result.Header().Values("Set-Cookie")) != 2 || strings.Contains(result.Body.String(), "signed_up") {
		t.Fatalf("%d %s", result.Code, result.Body.String())
	}
}

func TestStatsCancellationReachesUpstream(t *testing.T) {
	entered, observed, cleanup := make(chan struct{}), make(chan struct{}), make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte(strings.Split(emptyStream, "\n")[0] + "\n"))
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
	request, _ := http.NewRequestWithContext(ctx, "GET", downstream.URL+"/api/stats", nil)
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
		t.Fatal("upstream not entered")
	}
	started := time.Now()
	cancel()
	select {
	case <-observed:
		t.Logf("stats read cancellation %v", time.Since(started))
	case <-time.After(2 * time.Second):
		t.Fatal("actual upstream cancellation missing")
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("downstream still running")
	}
}

func TestUpstreamErrorsCannotExposePrivateStats(t *testing.T) {
	validation := `{"detail":[{"type":"literal_error","loc":["query","range"],"msg":"Input should be '30d', '1y' or 'all'","input":"bad","ctx":{"expected":"'30d', '1y' or 'all'"}}]}`
	for _, c := range []struct {
		name     string
		status   int
		body     string
		expected int
	}{
		{"original authentication", 401, `{"detail":"请先登录"}`, 401},
		{"original range validation", 422, validation, 422},
		{"private field inside error", 422, strings.Replace(validation, `"type":`, `"raw_stats":{"private":42},"type":`, 1), 502},
		{"private field inside context", 422, strings.Replace(validation, `"expected":`, `"raw_stats":42,"expected":`, 1), 502},
		{"missing service auth", 404, `{"detail":"Not Found"}`, 502},
		{"internal failure", 500, `{"raw_stats":42}`, 502},
		{"redirect", 302, `{"detail":"redirect"}`, 502},
	} {
		func() {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(c.status); w.Write([]byte(c.body)) }))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			result := httptest.NewRecorder()
			NewHandler(target, http.DefaultTransport, "core").ServeHTTP(result, httptest.NewRequest("GET", "/api/stats", nil))
			if result.Code != c.expected || strings.Contains(result.Body.String(), "raw_stats") {
				t.Fatalf("%s: got %d %s", c.name, result.Code, result.Body.String())
			}
		}()
	}
}
