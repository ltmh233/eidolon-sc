// SPDX-License-Identifier: AGPL-3.0-or-later
package achievements_test

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"eidolon/core/internal/achievements"
	"eidolon/core/internal/catalog"
)

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestPublicHTTPPublishesOnlyCompleteValidatedResults(t *testing.T) {
	cases := []struct {
		name          string
		status        int
		content, body string
		want          int
		output        string
	}{
		{"complete", 200, "application/x-ndjson", valid(), 200, `"progress":9007199254740993`},
		{"semantic", 200, "application/x-ndjson", strings.Replace(valid(), `["i","9007199254740993"]`, `["s","bad"]`, 1), 500, "Internal Server Error"},
		{"truncated semantic", 200, "application/x-ndjson", strings.Replace(strings.TrimSuffix(valid(), "[\"end\"]\n"), `["i","9007199254740993"]`, `["s","bad"]`, 1), 502, `{"detail":"SC achievements unavailable"}`},
		{"lone surrogate error detail", 401, "application/json", `{"detail":"\ud800"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"invalid UTF8 error detail", 401, "application/json", "{\"detail\":\"\xff\"}", 502, `{"detail":"SC achievements unavailable"}`},
		{"valid surrogate pair detail", 401, "application/json", `{"detail":"\ud83d\ude00"}`, 401, `{"detail":"\ud83d\ude00"}`},
		{"literal escaped backslash detail", 401, "application/json", `{"detail":"\\ud800"}`, 401, `{"detail":"\\ud800"}`},
		{"legacy envelope", 500, "application/json", `{"contract":"sc-character-achievements-v1","error":"legacy_read"}`, 500, "Internal Server Error"},
		{"unsupported", 503, "application/json", `{"contract":"sc-character-achievements-v1","error":"unsupported_contract"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"unknown500", 500, "application/json", `{"detail":"secret"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"unexpected envelope", 500, "application/json", `{"contract":"sc-character-achievements-v1","error":"legacy_read","value":"secret"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"duplicate envelope key", 500, "application/json", `{"contract":"sc-character-achievements-v1","error":"legacy_read","error":"legacy_read"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"old Python or missing private credential", 404, "application/json", `{"detail":"Not Found"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"unknown character", 404, "application/json", `{"detail":"角色不存在"}`, 404, `{"detail":"角色不存在"}`},
		{"unrecognized404", 404, "application/json", `{"detail":"secret"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"user", 401, "application/json", `{"detail":"请先登录"}`, 401, `{"detail":"请先登录"}`},
		{"origin", 403, "application/json", `{"detail":"origin denied"}`, 403, `{"detail":"origin denied"}`},
		{"consent", 428, "application/json", `{"detail":"consent"}`, 428, `{"detail":"consent"}`},
		{"redirect", 302, "application/json", `{"detail":"secret"}`, 502, `{"detail":"SC achievements unavailable"}`},
		{"wrong content type", 200, "application/json", valid(), 502, `{"detail":"SC achievements unavailable"}`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			calls := 0
			transport := roundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				if r.URL.Path != "/api/_core/achievements/mano" || r.Header.Get("Authorization") != "Bearer trusted" || r.Header.Get("Cookie") != "session=real" || r.Header.Get("Origin") != "http://localhost" || r.Header.Get("X-Work-ID") != "sc" {
					t.Fatal("private identity forwarding")
				}
				h := http.Header{"Content-Type": {c.content}, "Cache-Control": {"no-store"}, "Set-Cookie": {"a=1; HttpOnly", "b=2; HttpOnly"}}
				return &http.Response{StatusCode: c.status, Header: h, Body: io.NopCloser(strings.NewReader(c.body))}, nil
			})
			upstream, _ := url.Parse("http://private.invalid")
			native := achievements.NewHandler(upstream, transport, "trusted")
			public := catalog.Handler(&catalog.Registry{}, upstream, transport, nil, nil, nil, nil, nil, native)
			q := httptest.NewRequest("GET", "/api/characters/mano/achievements?work_id=sc&user_id=ignored", nil)
			for k, v := range map[string]string{"Cookie": "session=real", "Authorization": "Bearer untrusted", "Origin": "http://localhost", "X-Work-ID": "sc"} {
				q.Header.Set(k, v)
			}
			w := httptest.NewRecorder()
			public.ServeHTTP(w, q)
			if w.Code != c.want || !strings.Contains(w.Body.String(), c.output) || strings.Contains(w.Body.String(), "secret") || calls != 1 {
				t.Fatalf("public contract status=%d calls=%d body=%s", w.Code, calls, w.Body.String())
			}
			if w.Header().Get("Cache-Control") != "no-store" || len(w.Header().Values("Set-Cookie")) != 2 {
				t.Fatal("cookie/cache contract")
			}
			if c.want == 500 && (w.Body.String() != c.output || w.Header().Get("Content-Type") != "text/plain; charset=utf-8") {
				t.Fatal("legacy500 shape")
			}
		})
	}
}

func TestCancellationClosesPrivateBodyWithoutPublishing(t *testing.T) {
	reader, writer := io.Pipe()
	defer writer.Close()
	started := make(chan struct{})
	done := make(chan struct{})
	upstream, _ := url.Parse("http://private.invalid")
	transport := roundTrip(func(r *http.Request) (*http.Response, error) {
		close(started)
		go func() { <-r.Context().Done(); reader.CloseWithError(r.Context().Err()) }()
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"application/x-ndjson"}}, Body: reader}, nil
	})
	handler := achievements.NewHandler(upstream, transport, "trusted")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	q := httptest.NewRequest("GET", "/api/characters/mano/achievements", nil).WithContext(ctx)
	w := httptest.NewRecorder()
	go func() { handler.ServeHTTP(w, q); close(done) }()
	<-started
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("private body survived cancellation")
	}
	if w.Body.Len() != 0 {
		t.Fatal("partial public response on cancellation")
	}
}

func TestTCPFreshConnectionsNoRetryAndPrivateBlocking(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1 for real TCP acceptance")
	}
	var calls atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Path != "/api/_core/achievements/mano" || !r.Close {
			t.Error("must use one fresh private HTTP/1 request")
		}
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte(valid()))
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	native := achievements.NewHandler(target, http.DefaultTransport, "trusted")
	public := httptest.NewServer(catalog.Handler(&catalog.Registry{}, target, http.DefaultTransport, nil, nil, nil, nil, nil, native))
	defer public.Close()
	for range 2 {
		response, e := http.Get(public.URL + "/api/characters/mano/achievements")
		if e != nil {
			t.Fatal(e)
		}
		io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if response.StatusCode != 200 {
			t.Fatal(response.StatusCode)
		}
	}
	for _, path := range []string{"/api/_core/achievements/mano", "/api/%5fcore/achievements/mano"} {
		response, e := http.Get(public.URL + path)
		if e != nil {
			t.Fatalf("path=%s: %v", path, e)
		}
		response.Body.Close()
		if response.StatusCode != 404 {
			t.Fatalf("path=%s status=%d", path, response.StatusCode)
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("upstream attempts=%d", calls.Load())
	}
}

func TestTCPCancellationReachesPrivateRequest(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1 for real TCP cancellation")
	}
	started := make(chan struct{})
	observed := make(chan struct{})
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte(meta + "\n"))
		w.(http.Flusher).Flush()
		close(started)
		select {
		case <-r.Context().Done():
			close(observed)
		case <-release: // Independent failure cleanup must never count as observed cancellation.
		}
	}))
	target, _ := url.Parse(upstream.URL)
	public := httptest.NewServer(achievements.NewHandler(target, http.DefaultTransport, "trusted"))
	defer func() {
		close(release)
		public.CloseClientConnections()
		public.Close()
		upstream.CloseClientConnections()
		upstream.Close()
	}()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, "GET", public.URL+"/api/characters/mano/achievements", nil)
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		response, _ := http.DefaultClient.Do(request)
		if response != nil {
			response.Body.Close()
		}
	}()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("private TCP request did not start")
	}
	cancel()
	select {
	case <-observed:
	case <-time.After(2 * time.Second):
		t.Fatal("private request did not observe downstream cancellation")
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("public client did not finish")
	}
}
