// SPDX-License-Identifier: AGPL-3.0-or-later
package giftspanel_test

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"eidolon/core/internal/catalog"
	"eidolon/core/internal/giftspanel"
)

const nonce = "0123456789abcdef0123456789abcdef"
const base = `{"open":true,"draws":9223372036854775807,"max_draws":7,"backpack":[],"reset_cards":0,"next_draw_at":null,"resettable":[],"characters":[],"rewarded_at":null}`

func tcp(t *testing.T) {
	t.Helper()
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1 for real TCP acceptance")
	}
}

func TestPublicDialogValidatesEveryPhaseBeforePublishing(t *testing.T) {
	tcp(t)
	cases := []struct {
		name string
		want int
	}{
		{"normal", 200}, {"early legacy", 500}, {"late legacy", 500},
		{"bad hello", 502}, {"bad nonce", 502}, {"bad profile", 502}, {"bool count", 502},
		{"duplicate count pair", 502}, {"bad scalar", 502}, {"bad time", 502},
		{"duplicate base key", 502}, {"extra base key", 502}, {"lone surrogate", 502},
		{"missing end", 502}, {"extra end", 502}, {"truncated semantic abort", 502},
		{"error phase1", 500}, {"error phase2", 502}, {"error phase3", 500},
		{"bad error phase", 502}, {"bad error code", 502}, {"missing error end", 502},
		{"old404", 502}, {"unrecognized500", 502}, {"redirect", 502}, {"auth401", 401},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var calls, states atomic.Int64
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != "GET" || r.URL.Path != "/api/_core/sc-gifts-panel" || !r.Close || r.Proto != "HTTP/1.1" || r.ContentLength != -1 || r.GetBody != nil || r.Header.Get("Expect") != "" || r.Header.Get("Authorization") != "Bearer dedicated" || r.Header.Get("Cookie") != "session=real" || r.Header.Get("Origin") != "https://foreign.example" {
					t.Error("private request/identity/HTTP1 contract")
				}
				if len(r.TransferEncoding) != 1 || r.TransferEncoding[0] != "chunked" {
					t.Error("request must be chunked")
				}
				if e := http.NewResponseController(w).EnableFullDuplex(); e != nil {
					t.Error(e)
					return
				}
				w.Header().Add("Set-Cookie", "a=1; HttpOnly")
				w.Header().Add("Set-Cookie", "b=2; SameSite=lax")
				w.Header().Set("Cache-Control", "no-store")
				w.Header().Set("Connection", "close")
				if c.name == "old404" || c.name == "auth401" || c.name == "redirect" || c.name == "unrecognized500" {
					status := map[string]int{"old404": 404, "auth401": 401, "redirect": 302, "unrecognized500": 500}[c.name]
					w.Header().Set("Content-Type", "application/json")
					w.Header().Set("Location", "/api/gifts")
					w.WriteHeader(status)
					fmt.Fprint(w, `{"detail":"failure"}`)
					return
				}
				w.Header().Set("Content-Type", "application/x-ndjson")
				reader := bufio.NewReader(r.Body)
				read := func() string {
					line, e := reader.ReadString('\n')
					if e != nil {
						return ""
					}
					return strings.TrimSpace(line)
				}
				write := func(line string) { fmt.Fprintln(w, line); w.(http.Flusher).Flush() }
				if read() != `["begin","sc-gifts-panel-v1"]` {
					return
				}
				if c.name == "bad hello" {
					write(`["hello","wrong","sc","http1-duplex-v1"]`)
					return
				}
				write(`["hello","sc-gifts-panel-v1","sc","http1-duplex-v1"]`)
				if strings.HasPrefix(c.name, "error phase1") || c.name == "bad error phase" || c.name == "bad error code" || c.name == "missing error end" {
					errorRow := `["error",1,"legacy"]`
					if c.name == "bad error phase" {
						errorRow = `["error",0,"legacy"]`
					}
					if c.name == "bad error code" {
						errorRow = `["error",1,"secret"]`
					}
					write(errorRow)
					if c.name != "missing error end" {
						write(`["end"]`)
					}
					if tail, e := io.ReadAll(reader); c.name == "error phase1" && (e != nil || len(tail) != 0) {
						t.Error("terminal error did not close control writer with precise EOF")
					}
					return
				}
				header := `["daily",1,"` + nonce + `",true,1,["py311-sql-v1",4300]]`
				if c.name == "bad nonce" {
					header = strings.Replace(header, nonce, "ABC", 1)
				}
				if c.name == "bad profile" {
					header = strings.Replace(header, "4300", "639", 1)
				}
				if c.name == "bool count" {
					header = strings.Replace(header, "true,1,", "true,true,", 1)
				}
				write(header)
				write(`["daily-time","2026-09-25T16:00:00+00:00"]`)
				count := `["i","9007199254740993"]`
				if c.name == "early legacy" || c.name == "truncated semantic abort" {
					count = `["s","bad"]`
				}
				write(`["daily-counts",` + count + `,["i","0"]]`)
				write(`["task","one","turns","9007199254740994","1"]`)
				write(`["ready",1,"` + nonce + `"]`)
				control := read()
				if strings.HasPrefix(control, `["abort"`) {
					if c.name != "truncated semantic abort" {
						write(`["end"]`)
					}
					return
				}
				if control != `["next",1,"`+nonce+`"]` {
					return
				}
				if c.name == "error phase2" {
					write(`["error",2,"unsupported"]`)
					write(`["end"]`)
					return
				}
				n := 2
				if c.name == "duplicate count pair" {
					n = 3
				}
				stages := 0
				if c.name == "bad time" {
					stages = 1
				}
				write(fmt.Sprintf(`["all",2,"%s",true,1,%d,%d,["py311-sql-v1",4300]]`, nonce, n, stages))
				write(`["tiers",["close","intimate"]]`)
				id := "characters"
				if c.name == "late legacy" {
					id = "draws"
				}
				write(`["achievement","` + id + `",[["3","1"]]]`)
				count = `["i","1"]`
				if c.name == "bad scalar" {
					count = `["i","01"]`
				}
				if c.name == "late legacy" {
					write(`["count",["s","draws"],["s",""],["s","bad"]]`)
				} else {
					write(`["count",["s","turns"],["s","retired"],` + count + `]`)
				}
				write(`["count",["s","turns"],["b",""],["i","1"]]`)
				if c.name == "duplicate count pair" {
					write(`["count",["s","turns"],["b",""],["i","2"]]`)
				}
				if c.name == "bad time" {
					write(`["stage",["s","old"],["i","1"],["2026-10-29T00:00:00",false,"0"]]`)
				}
				write(`["ready",2,"` + nonce + `"]`)
				control = read()
				if control != `["next",2,"`+nonce+`"]` {
					return
				}
				states.Add(1)
				if c.name == "error phase3" {
					write(`["error",3,"legacy"]`)
					write(`["end"]`)
					return
				}
				b := base
				if c.name == "duplicate base key" {
					b = strings.Replace(b, `"draws":`, `"draws":1,"draws":`, 1)
				}
				if c.name == "extra base key" {
					b = strings.Replace(b, `"open":`, `"user":"secret","open":`, 1)
				}
				if c.name == "lone surrogate" {
					b = strings.Replace(b, `"backpack":[]`, `"backpack":["\ud800"]`, 1)
				}
				write(`["state",3,"` + nonce + `",` + b + `]`)
				control = read()
				if !strings.HasPrefix(control, `["finish",3,`) {
					return
				}
				if tail, e := io.ReadAll(reader); e != nil || len(tail) != 0 {
					t.Error("finish requires precise control EOF")
					return
				}
				if c.name != "missing end" {
					write(`["end"]`)
				}
				if c.name == "extra end" {
					write(`["end"]`)
				}
			}))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			public := httptest.NewServer(catalog.Handler(&catalog.Registry{}, target, http.DefaultTransport, nil, nil, nil, nil, nil, nil, giftspanel.NewHandler(target, http.DefaultTransport.(*http.Transport), "dedicated")))
			defer public.Close()
			req, _ := http.NewRequest("GET", public.URL+"/api/gifts", strings.NewReader("ignored client body"))
			req.Header.Set("Cookie", "session=real")
			req.Header.Set("Origin", "https://foreign.example")
			req.Header.Set("Authorization", "Bearer user supplied")
			response, e := http.DefaultClient.Do(req)
			if e != nil {
				t.Fatal(e)
			}
			defer response.Body.Close()
			data, _ := io.ReadAll(response.Body)
			if response.StatusCode != c.want || calls.Load() != 1 {
				t.Fatalf("public scenario=%s status=%d calls=%d body=%s", c.name, response.StatusCode, calls.Load(), data)
			}
			cookies := response.Header.Values("Set-Cookie")
			if c.want == 500 {
				if len(cookies) != 0 || string(data) != "Internal Server Error" {
					t.Fatal("legacy Cookie/body contract")
				}
			} else if len(cookies) != 2 || cookies[0] != "a=1; HttpOnly" || cookies[1] != "b=2; SameSite=lax" {
				t.Fatal("ordered real Cookie forwarding")
			}
			if c.want == 200 {
				if !strings.Contains(string(data), `"draws":9223372036854775807`) || !strings.Contains(string(data), `"progress":9007199254740993`) || !strings.Contains(string(data), `"progress":2`) {
					t.Fatal("exact base/min/global calculation", string(data))
				}
			}
			if c.name == "early legacy" && states.Load() != 0 {
				t.Fatal("early error ran state")
			}
			if c.name == "late legacy" && states.Load() != 1 {
				t.Fatal("late error skipped state")
			}
		})
	}
}

func TestPublicCancellationClosesDialogAndNeverPublishesPartialSuccess(t *testing.T) {
	tcp(t)
	started, closed := make(chan struct{}), make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NewResponseController(w).EnableFullDuplex()
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Connection", "close")
		fmt.Fprintln(w, `["hello","sc-gifts-panel-v1","sc","http1-duplex-v1"]`)
		w.(http.Flusher).Flush()
		close(started)
		io.Copy(io.Discard, r.Body)
		close(closed)
	}))
	defer upstream.Close()
	defer upstream.CloseClientConnections()
	target, _ := url.Parse(upstream.URL)
	handler := giftspanel.NewHandler(target, http.DefaultTransport.(*http.Transport), "dedicated")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request := httptest.NewRequest("GET", "/api/gifts", nil).WithContext(ctx)
	out := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { handler.ServeHTTP(out, request); close(done) }()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("private dialog did not start")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("handler leaked after cancellation")
	}
	select {
	case <-closed:
	case <-time.After(2 * time.Second):
		t.Fatal("request writer survived cancellation")
	}
	if out.Code == 200 || strings.Contains(out.Body.String(), `"open"`) {
		t.Fatal("partial success published")
	}
}

func TestDedicatedClientDisablesHTTP2EvenWithTLSCapableSourceTransport(t *testing.T) {
	tcp(t)
	protocol := make(chan string, 1)
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		protocol <- r.Proto
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(401)
		fmt.Fprint(w, `{"detail":"login"}`)
	}))
	upstream.EnableHTTP2 = true
	upstream.StartTLS()
	defer upstream.Close()
	defer upstream.CloseClientConnections()
	target, _ := url.Parse(upstream.URL)
	trusted := upstream.Client().Transport.(*http.Transport).Clone()
	trusted.ForceAttemptHTTP2 = true
	// Only this supplied test transport trusts httptest's certificate. The panel
	// clones it and still removes H2/ambient proxy behavior before making a call.
	output := httptest.NewRecorder()
	giftspanel.NewHandler(target, trusted, "dedicated").ServeHTTP(output, httptest.NewRequest("GET", "/api/gifts", nil))
	if output.Code != 401 {
		t.Fatalf("status=%d body=%s", output.Code, output.Body.String())
	}
	select {
	case p := <-protocol:
		if p != "HTTP/1.1" {
			t.Fatal(p)
		}
	case <-time.After(time.Second):
		t.Fatal("TLS upstream was not reached")
	}
}

func TestInvalidEarlyResponseClosesUnreadUploadPromptly(t *testing.T) {
	tcp(t)
	for _, scenario := range []string{"wrong headers", "wrong hello"} {
		t.Run(scenario, func(t *testing.T) {
			release := make(chan struct{})
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				http.NewResponseController(w).EnableFullDuplex()
				w.Header().Set("Content-Type", "application/x-ndjson")
				w.Header().Set("Cache-Control", "no-store")
				w.Header().Set("Connection", "close")
				if scenario == "wrong headers" {
					w.Header().Set("Content-Type", "text/plain")
				}
				fmt.Fprintln(w, `["hello","wrong","sc","http1-duplex-v1"]`)
				w.(http.Flusher).Flush()
				// Do not read r.Body. Independent cleanup releases the server even
				// if the production client fails to cancel its upload or response.
				<-release
			}))
			defer upstream.Close()
			defer upstream.CloseClientConnections()
			defer close(release)
			target, _ := url.Parse(upstream.URL)
			out, done := httptest.NewRecorder(), make(chan struct{})
			go func() {
				giftspanel.NewHandler(target, http.DefaultTransport.(*http.Transport), "dedicated").ServeHTTP(out, httptest.NewRequest("GET", "/api/gifts", nil))
				close(done)
			}()
			select {
			case <-done:
				if out.Code != 502 {
					t.Fatal(out.Code)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("invalid early response waited for upload or sixty-second deadline")
			}
		})
	}
}
