package catalog

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(q *http.Request) (*http.Response, error) { return f(q) }
func registry(t *testing.T) *Registry {
	t.Helper()
	r, e := Load("../../packs", "../../testdata/packs")
	if e != nil {
		t.Fatal(e)
	}
	return r
}

func TestExplicitBodyScopeDoesNotFallBackToSC(t *testing.T) {
	u, _ := url.Parse("http://python:8000")
	h := Handler(registry(t), u, roundTrip(func(*http.Request) (*http.Response, error) { t.Fatal("DLC request reached Python"); return nil, nil }))
	for _, body := range []string{`{"character_id":"mano","work_id":"demo"}`, `{"work_id":null}`, `{"requestData":{"character_id":"mano","work_id":"demo"}}`} {
		q := httptest.NewRequest("POST", "/api/offer", strings.NewReader(body))
		w := httptest.NewRecorder()
		h.ServeHTTP(w, q)
		if w.Code != 409 {
			t.Fatal(w.Code, w.Body)
		}
	}
}

// In-memory sockets exercise net/http framing and flushing without requiring an
// OS listening port. The separate TCP test still runs in CI.
type pipeListener struct {
	connections chan net.Conn
	done        chan struct{}
}

func (p *pipeListener) Accept() (net.Conn, error) {
	select {
	case c := <-p.connections:
		return c, nil
	case <-p.done:
		return nil, net.ErrClosed
	}
}
func (p *pipeListener) Close() error {
	select {
	case <-p.done:
	default:
		close(p.done)
	}
	return nil
}
func (p *pipeListener) Addr() net.Addr { return pipeAddr("memory") }

type pipeAddr string

func (p pipeAddr) Network() string { return "pipe" }
func (p pipeAddr) String() string  { return string(p) }
func memoryHTTP(t *testing.T, h http.Handler) *http.Transport {
	t.Helper()
	l := &pipeListener{connections: make(chan net.Conn), done: make(chan struct{})}
	s := &http.Server{Handler: h}
	go s.Serve(l)
	tr := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		client, server := net.Pipe()
		select {
		case l.connections <- server:
			return client, nil
		case <-ctx.Done():
			client.Close()
			server.Close()
			return nil, ctx.Err()
		}
	}}
	t.Cleanup(func() { tr.CloseIdleConnections(); s.Close() })
	return tr
}

func TestHTTPStreamingAcrossGateway(t *testing.T) {
	release := make(chan struct{})
	defer close(release)
	legacy := memoryHTTP(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-release
		io.WriteString(w, "data: second\n\n")
	}))
	u, _ := url.Parse("http://python")
	tr := memoryHTTP(t, Handler(registry(t), u, legacy))
	c := &http.Client{Transport: tr, Timeout: 2 * time.Second}
	res, err := c.Get("http://gateway/api/conversations/id/turns")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	first := make([]byte, len("data: first\n\n"))
	if _, err := io.ReadFull(res.Body, first); err != nil || string(first) != "data: first\n\n" {
		t.Fatalf("SSE buffered: %q, %v", first, err)
	}
}

func BenchmarkDLCCharacters(b *testing.B) {
	r, err := Load("../../packs", "../../testdata/packs")
	if err != nil {
		b.Fatal(err)
	}
	u, _ := url.Parse("http://python")
	h := Handler(r, u, http.DefaultTransport)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		request(h, "GET", "/api/works/demo/characters")
	}
}
func request(h http.Handler, method, path string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(method, path, nil))
	return w
}

func TestCatalogIsolationAndLegacyContract(t *testing.T) {
	legacy := `[{"id":"mano","name":"櫻木真乃","avatar_url":"/api/characters/mano/avatar","birthday":"04-25","stand":null}]`
	u, _ := url.Parse("http://python:8000")
	called := 0
	transport := roundTrip(func(q *http.Request) (*http.Response, error) {
		called++
		if q.URL.Path != "/api/characters" {
			t.Fatalf("unexpected legacy request %s", q.URL)
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(legacy))}, nil
	})
	h := Handler(registry(t), u, transport)
	old := request(h, "GET", "/api/characters")
	if old.Code != 200 || old.Body.String() != legacy {
		t.Fatalf("legacy contract changed: %s", old.Body)
	}
	sc := request(h, "GET", "/api/works/sc/characters")
	demo := request(h, "GET", "/api/works/demo/characters")
	if sc.Code != 200 || !strings.Contains(sc.Body.String(), `"id":"sc:mano"`) || !strings.Contains(sc.Body.String(), `"birthday":"04-25"`) {
		t.Fatal(sc.Body)
	}
	if demo.Code != 200 || !strings.Contains(demo.Body.String(), `"id":"demo:mano"`) || strings.Contains(demo.Body.String(), "櫻木") || strings.Contains(demo.Body.String(), "sheet") {
		t.Fatal(demo.Body)
	}
	world := request(h, "GET", "/api/works/demo/world")
	if world.Code != 200 || !strings.Contains(world.Body.String(), "星灯学院") {
		t.Fatal(world.Body)
	}
	if called != 2 {
		t.Fatalf("DLC accessed SC backend %d", called)
	}
	for _, path := range []string{"/api/works/demo/rag", "/api/works/sc/world", "/api/works/demo/memories"} {
		if w := request(h, "GET", path); w.Code != 501 {
			t.Fatalf("%s: %d", path, w.Code)
		}
	}
	for _, path := range []string{"/api/works/nope/characters", "/api/works/demo/characters/mano"} {
		if w := request(h, "GET", path); w.Code != 404 {
			t.Fatal(w.Code)
		}
	}
	if w := request(h, "POST", "/api/works/demo/conversations"); w.Code != 405 {
		t.Fatal(w.Code)
	}
	if w := request(h, "GET", "/api/characters?work_id=demo"); w.Code != 409 {
		t.Fatal(w.Code)
	}
	if w := request(h, "GET", "/api/works/demo/world?work_id=sc"); w.Code != 400 {
		t.Fatal(w.Code)
	}
	list := request(h, "GET", "/api/works")
	if list.Code != 200 || !strings.Contains(list.Body.String(), `"default_work_id":"sc"`) || !strings.Contains(list.Body.String(), `"rag_scope":"unavailable"`) {
		t.Fatal(list.Body)
	}
}

func TestProxyPreservesAuthenticationAndStreaming(t *testing.T) {
	u, _ := url.Parse("http://python:8000")
	transport := roundTrip(func(q *http.Request) (*http.Response, error) {
		if q.Header.Get("Cookie") != "eidolon_session=opaque" || q.Header.Get("Origin") != "https://example.test" || q.Method != "POST" {
			t.Fatal("authentication changed")
		}
		data, _ := io.ReadAll(q.Body)
		if string(data) != "original body" {
			t.Fatal("body changed")
		}
		return &http.Response{StatusCode: 428, Header: http.Header{"Set-Cookie": []string{"eidolon_session=renewed; HttpOnly; SameSite=Lax"}, "Content-Type": []string{"text/event-stream"}}, Body: io.NopCloser(strings.NewReader("data: test\n\n"))}, nil
	})
	q := httptest.NewRequest("POST", "/api/conversations/123/turns", strings.NewReader("original body"))
	q.Header.Set("Cookie", "eidolon_session=opaque")
	q.Header.Set("Origin", "https://example.test")
	w := httptest.NewRecorder()
	Handler(registry(t), u, transport).ServeHTTP(w, q)
	if w.Code != 428 || w.Header().Get("Set-Cookie") == "" || w.Body.String() != "data: test\n\n" || !w.Flushed {
		t.Fatal("response contract changed", w)
	}
}

func TestBadUpstreamFailsClosed(t *testing.T) {
	u, _ := url.Parse("http://python:8000")
	for _, body := range []string{`null`, `{}`, `[{"id":"../sc"}]`, `[{"id":"mano"},{"id":"mano"}]`, strings.Repeat("x", maxPackage+1)} {
		h := Handler(registry(t), u, roundTrip(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(body)), Header: make(http.Header)}, nil
		}))
		if w := request(h, "GET", "/api/works/sc/characters"); w.Code != 502 {
			t.Fatalf("got %d", w.Code)
		}
	}
	h := Handler(registry(t), u, roundTrip(func(*http.Request) (*http.Response, error) { return nil, fmt.Errorf("offline") }))
	if w := request(h, "GET", "/api/works/demo/characters"); w.Code != 200 {
		t.Fatal("DLC depends on Python")
	}
	if w := request(h, "GET", "/api/works/sc/characters"); w.Code != 502 {
		t.Fatal(w.Code)
	}
}

func TestPackageValidation(t *testing.T) {
	valid := `{"schema_version":1,"work_id":"demo","name":"Demo","source":"content","world":"Only Demo","characters":[{"id":"mano","name":"Demo Mano","sheet":"Demo sheet"}]}`
	for _, body := range []string{
		strings.Replace(valid, `"schema_version":1`, `"schema_version":2`, 1),
		strings.Replace(valid, `"work_id":"demo"`, `"work_id":"../demo"`, 1),
		strings.Replace(valid, `"source":"content"`, `"source":"legacy-sc"`, 1),
		strings.Replace(valid, `"world":"Only Demo"`, `"world":""`, 1),
		strings.Replace(valid, `"sheet":"Demo sheet"`, `"secret":"wrong"`, 1),
		strings.Replace(valid, `"characters":[`, `"characters":[{"id":"mano","name":"Duplicate","sheet":"x"},`, 1),
		valid + `{}`, strings.Repeat("x", maxPackage+1),
	} {
		root := t.TempDir()
		os.Mkdir(filepath.Join(root, "bad"), 0700)
		os.WriteFile(filepath.Join(root, "bad", "work.json"), []byte(body), 0600)
		if _, err := Load("../../packs", root); err == nil {
			t.Fatal("accepted invalid package")
		}
	}
	if _, err := Load("../../packs", "../../packs"); err == nil {
		t.Fatal("duplicate work accepted")
	}
	if _, err := Load("../../testdata/packs"); err == nil {
		t.Fatal("missing default accepted")
	}
}

func TestRealHTTPBoundary(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1 for TCP integration (requires socket permission)")
	}
	// Real sockets exercise the transport and reverse proxy in addition to handler tests.
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Set-Cookie", "eidolon_session=unchanged; HttpOnly")
		io.WriteString(w, `[{"id":"mano","name":"SC"}]`)
	}))
	defer upstream.Close()
	u, _ := url.Parse(upstream.URL)
	gateway := httptest.NewServer(Handler(registry(t), u, http.DefaultTransport))
	defer gateway.Close()
	for _, path := range []string{"/api/characters", "/api/works/sc/characters", "/api/works/demo/characters", "/api/works/demo/world"} {
		res, err := http.Get(gateway.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, res.Body)
		res.Body.Close()
		if res.StatusCode != 200 {
			t.Fatal(path, res.StatusCode)
		}
	}
}
