// SPDX-License-Identifier: AGPL-3.0-or-later
package affinity

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

func TestRawStateComputedAndCookiesPreserved(t *testing.T) {
	now, _ := time.Parse(time.RFC3339Nano, "2026-10-27T12:00:00.123456Z")
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/_core/affinity/mano" || r.Header.Get("Authorization") != "Bearer "+strings.Repeat("1", 64) || r.Header.Get("Cookie") != "session=test" {
			t.Error("service or session binding missing")
		}
		w.Header().Add("Set-Cookie", "one=test; HttpOnly")
		w.Header().Add("Set-Cookie", "two=test; HttpOnly")
		json.NewEncoder(w).Encode(Snapshot{Version: 1, WorkID: "sc", CharacterID: "mano", Enabled: true, ObservedAt: now.Format(time.RFC3339Nano), State: &Stored{Value: 0, UpdatedAtUS: now.Add(-12 * time.Hour).UnixMicro(), Cooling: true}})
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	q := httptest.NewRequest("GET", "/api/characters/mano/affinity", nil)
	q.Header.Set("Cookie", "session=test")
	q.Header.Set("Authorization", "Bearer client-cannot-choose-service-identity")
	result := httptest.NewRecorder()
	NewHandler(target, http.DefaultTransport, strings.Repeat("1", 64)).ServeHTTP(result, q)
	if result.Code != 200 || len(result.Header().Values("Set-Cookie")) != 2 || strings.Contains(result.Body.String(), "value") {
		t.Fatalf("public response leaked or lost cookies: %d %s", result.Code, result.Body.String())
	}
	var status Status
	if err := json.Unmarshal(result.Body.Bytes(), &status); err != nil || status.Tier != "cold" || status.CooldownUntil == nil || *status.CooldownUntil != "2026-10-28T00:00:00.123456+00:00" {
		t.Fatalf("not computed: %+v %v", status, err)
	}
}

func TestContractErrorsNeverBecomePublicState(t *testing.T) {
	cases := []struct {
		status   int
		body     string
		expected int
	}{
		{401, `{"detail":"请先登录"}`, 401},
		{401, `{"detail":null}`, 502},
		{404, `{"detail":"Not Found"}`, 502},
		{404, `{"detail":"角色不存在"}`, 404},
		{401, `{"detail":"请先登录","raw_value":7}`, 502},
		{200, `{"version":1,"work_id":"sc","character_id":"mano","enabled":false,"state":null}`, 502},
		{200, `{"version":1,"work_id":"demo","character_id":"mano","enabled":false,"state":null,"observed_at":"2026-10-27T00:00:00Z"}`, 502},
		{500, `{"raw_value":7}`, 502},
		{302, `{"detail":"redirect"}`, 502},
	}
	for _, c := range cases {
		func() {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(c.status); w.Write([]byte(c.body)) }))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			result := httptest.NewRecorder()
			NewHandler(target, http.DefaultTransport, "test").ServeHTTP(result, httptest.NewRequest("GET", "/api/characters/mano/affinity", nil))
			if result.Code != c.expected || strings.Contains(result.Body.String(), "raw_value") {
				t.Fatalf("status=%d body=%s: got %d %s", c.status, c.body, result.Code, result.Body.String())
			}
		}()
	}
}

func TestCancellationReachesRawStateRequest(t *testing.T) {
	entered, observed, cleanup := make(chan struct{}), make(chan struct{}), make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		select {
		case <-r.Context().Done():
			close(observed)
		case <-cleanup:
		}
	}))
	target, _ := url.Parse(upstream.URL)
	downstream := httptest.NewServer(NewHandler(target, http.DefaultTransport, "test"))
	defer func() { close(cleanup); downstream.Close(); upstream.Close() }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, "GET", downstream.URL+"/api/characters/mano/affinity", nil)
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
		t.Fatal("upstream not reached")
	}
	started := time.Now()
	cancel()
	select {
	case <-observed:
		t.Logf("raw request cancelled in %v", time.Since(started))
	case <-time.After(2 * time.Second):
		t.Fatal("upstream did not observe cancellation")
	}
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("client still waiting")
	}
}
