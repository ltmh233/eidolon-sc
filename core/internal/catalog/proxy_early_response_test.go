// SPDX-License-Identifier: AGPL-3.0-or-later
package catalog

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sync"
	"testing"
	"time"
)

// Pause only between actual request-body reads, exposing the legal scheduling
// window where an early response reaches the proxy before its upload completes.
// The response must not cause net/http to close the body behind Transport's reader.
func TestTCPEarlyResponseDoesNotCloseConcurrentUpload(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1")
	}
	const expected = `{"detail":"request refused"}`
	releaseRead, releaseResponse := make(chan struct{}), make(chan struct{})
	var releaseReadOnce, releaseResponseOnce sync.Once
	release := func() {
		releaseReadOnce.Do(func() { close(releaseRead) })
		releaseResponseOnce.Do(func() { close(releaseResponse) })
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NewResponseController(w).EnableFullDuplex() // model a backend that rejects without consuming the upload
		var first [1]byte
		if _, err := io.ReadFull(r.Body, first[:]); err != nil {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Content-Length", fmt.Sprint(len(expected)))
		w.WriteHeader(403)
		w.(http.Flusher).Flush()
		select {
		case <-releaseResponse:
		case <-r.Context().Done():
			return
		}
		io.WriteString(w, expected)
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	readPaused, flushStarted, flushFinished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	readResult := make(chan error, 1)
	proxy := Handler(registry(t), target, http.DefaultTransport)
	gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.Body = &pausedUpload{ReadCloser: r.Body, paused: readPaused, resume: releaseRead, result: readResult}
		proxy.ServeHTTP(&observedFlush{ResponseWriter: w, started: flushStarted, finished: flushFinished}, r)
	}))
	defer gateway.Close()
	defer release() // unblock both servers even on assertion failure
	connection, err := net.Dial("tcp", gateway.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	connection.SetDeadline(time.Now().Add(5 * time.Second))
	io.WriteString(connection, "POST /api/proxy-early-response HTTP/1.1\r\nHost: local\r\nContent-Length: 2\r\n\r\nx")
	wait := func(ch <-chan struct{}, phase string) {
		t.Helper()
		select {
		case <-ch:
		case <-time.After(2 * time.Second):
			t.Fatalf("did not reach %s", phase)
		}
	}
	wait(readPaused, "second upload read")
	wait(flushStarted, "early-response flush")
	io.WriteString(connection, "y")
	wait(flushFinished, "early-response flushed")
	releaseReadOnce.Do(func() { close(releaseRead) })
	select {
	case err = <-readResult:
	case <-time.After(2 * time.Second):
		t.Fatal("upload reader did not resume")
	}
	releaseResponseOnce.Do(func() { close(releaseResponse) })
	if errors.Is(err, http.ErrBodyReadAfterClose) {
		t.Error("response flush closed the live request body behind the upload reader")
	}
	response, err := http.ReadResponse(bufio.NewReader(connection), nil)
	if err != nil {
		t.Fatalf("missing complete early response: %v", err)
	}
	defer response.Body.Close()
	data, err := io.ReadAll(response.Body)
	if err != nil || response.StatusCode != 403 || string(data) != expected {
		t.Fatalf("truncated rejection: status=%d bytes=%d error=%v", response.StatusCode, len(data), err)
	}
}

type pausedUpload struct {
	io.ReadCloser
	paused chan struct{}
	resume <-chan struct{}
	result chan<- error
	reads  int
}

func (b *pausedUpload) Read(p []byte) (int, error) {
	b.reads++
	if b.reads == 2 {
		close(b.paused)
		<-b.resume
		n, err := b.ReadCloser.Read(p)
		b.result <- err
		return n, err
	}
	return b.ReadCloser.Read(p)
}

type observedFlush struct {
	http.ResponseWriter
	started, finished chan struct{}
	once              sync.Once
}

func (w *observedFlush) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *observedFlush) FlushError() error {
	first := false
	w.once.Do(func() { close(w.started); first = true })
	err := http.NewResponseController(w.ResponseWriter).Flush()
	if first {
		close(w.finished)
	}
	return err
}

// An early rejection must arrive without reading an unbounded/unfinished upload.
// After the client closes, Transport reads/closes and the proxy must stop. No test cleanup
// channel counts as successful cancellation.
func TestTCPEarlyRejectionWithUnfinishedLargeUpload(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1")
	}
	for _, framing := range []string{"length", "chunked"} {
		t.Run(framing, func(t *testing.T) {
			const expected = `{"detail":"request refused"}`
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				http.NewResponseController(w).EnableFullDuplex()
				var first [1]byte
				if _, err := io.ReadFull(r.Body, first[:]); err != nil {
					return
				}
				w.Header().Set("Content-Length", fmt.Sprint(len(expected)))
				w.WriteHeader(403)
				io.WriteString(w, expected)
				w.(http.Flusher).Flush()
			}))
			defer upstream.Close()
			target, _ := url.Parse(upstream.URL)
			finished := make(chan struct{})
			observed := make(chan *observedUploadBody, 1)
			proxy := Handler(registry(t), target, http.DefaultTransport)
			gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				defer close(finished)
				body := &observedUploadBody{ReadCloser: r.Body, reading: make(chan struct{}), quiesced: make(chan struct{})}
				r.Body = body
				observed <- body
				proxy.ServeHTTP(w, r)
			}))
			defer gateway.Close()
			connection, err := net.Dial("tcp", gateway.Listener.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			defer connection.Close()
			connection.SetDeadline(time.Now().Add(2 * time.Second))
			header := "Content-Length: 4194304\r\n\r\nx"
			if framing == "chunked" {
				header = "Transfer-Encoding: chunked\r\n\r\n1\r\nx\r\n"
			}
			io.WriteString(connection, "POST /api/proxy-early-response HTTP/1.1\r\nHost: local\r\n"+header)
			response, err := http.ReadResponse(bufio.NewReader(connection), nil)
			if err != nil {
				t.Fatalf("rejection waited for unfinished upload: %v", err)
			}
			data, err := io.ReadAll(response.Body)
			response.Body.Close()
			if err != nil || response.StatusCode != 403 || string(data) != expected {
				t.Fatalf("incomplete rejection: status=%d bytes=%d error=%v", response.StatusCode, len(data), err)
			}
			body := <-observed
			select {
			case <-body.reading:
			case <-time.After(2 * time.Second):
				t.Fatal("upload never entered its unfinished second Read")
			}
			connection.Close()
			select {
			case <-body.quiesced:
			case <-time.After(2 * time.Second):
				t.Fatal("Transport upload Read/Close survived client disconnect")
			}
			select {
			case <-finished:
			case <-time.After(2 * time.Second):
				t.Fatal("proxy handler survived client disconnect")
			}
		})
	}
}

func TestTCPUploadAndResponseRemainStreaming(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1")
	}
	const size = 3 << 20
	completed := make(chan int64, 1)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NewResponseController(w).EnableFullDuplex()
		var first [1]byte
		if _, err := io.ReadFull(r.Body, first[:]); err != nil {
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		n, err := io.Copy(io.Discard, r.Body)
		if err != nil {
			return
		}
		completed <- n + 1
		io.WriteString(w, "data: done\n\n")
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	gateway := httptest.NewServer(Handler(registry(t), target, http.DefaultTransport))
	defer gateway.Close()
	connection, err := net.Dial("tcp", gateway.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	connection.SetDeadline(time.Now().Add(5 * time.Second))
	io.WriteString(connection, "POST /api/proxy-upload HTTP/1.1\r\nHost: local\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx\r\n")
	response, err := http.ReadResponse(bufio.NewReader(connection), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	first := make([]byte, len("data: first\n\n"))
	if _, err := io.ReadFull(response.Body, first); err != nil || string(first) != "data: first\n\n" {
		t.Fatalf("response buffered until upload completion: %v", err)
	}
	fmt.Fprintf(connection, "%x\r\n", size)
	if _, err := io.CopyN(connection, zeroReader{}, size); err != nil {
		t.Fatal(err)
	}
	io.WriteString(connection, "\r\n0\r\n\r\n")
	rest, err := io.ReadAll(response.Body)
	if err != nil || string(rest) != "data: done\n\n" {
		t.Fatalf("normal SSE completion changed: %v", err)
	}
	select {
	case count := <-completed:
		if count != size+1 {
			t.Fatalf("upload bytes=%d", count)
		}
	case <-time.After(time.Second):
		t.Fatal("upstream did not finish upload")
	}
}

type zeroReader struct{}

func (zeroReader) Read(p []byte) (int, error) { clear(p); return len(p), nil }

// Success requires Close to return AND every in-flight Read to exit. Handler
// completion alone cannot establish this for Transport's asynchronous upload.
type observedUploadBody struct {
	io.ReadCloser
	mu                sync.Mutex
	active, reads     int
	closed            bool
	reading, quiesced chan struct{}
	once              sync.Once
}

func (b *observedUploadBody) Read(p []byte) (int, error) {
	b.mu.Lock()
	b.active++
	b.reads++
	if b.reads == 2 {
		close(b.reading)
	}
	b.mu.Unlock()
	n, err := b.ReadCloser.Read(p)
	b.mu.Lock()
	b.active--
	b.signalQuiescence()
	b.mu.Unlock()
	return n, err
}
func (b *observedUploadBody) Close() error {
	err := b.ReadCloser.Close()
	b.mu.Lock()
	b.closed = true
	b.signalQuiescence()
	b.mu.Unlock()
	return err
}
func (b *observedUploadBody) signalQuiescence() {
	if b.closed && b.active == 0 {
		b.once.Do(func() { close(b.quiesced) })
	}
}
