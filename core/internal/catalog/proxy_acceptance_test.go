package catalog

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"testing"
	"time"
)

// A client disconnect must release upstream work both before response headers
// arrive and after an SSE stream has started. Use TCP, not a recorder.
func TestTCPProxyCancellation(t *testing.T) {
	if os.Getenv("EIDOLON_TCP_TESTS") != "1" {
		t.Skip("set EIDOLON_TCP_TESTS=1 for TCP integration")
	}
	for _, streaming := range []bool{false, true} {
		name := "before_headers"
		if streaming {
			name = "during_sse"
		}
		t.Run(name, func(t *testing.T) {
			started, stopped := make(chan struct{}), make(chan struct{})
			release := make(chan struct{})
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				close(started)
				if streaming {
					w.Header().Set("Content-Type", "text/event-stream")
					io.WriteString(w, "data: first\n\n")
					w.(http.Flusher).Flush()
				}
				select {
				case <-r.Context().Done():
					close(stopped)
				case <-release:
					// Cleanup must also work when cancellation propagation is broken.
				}
			}))
			defer upstream.Close()
			u, _ := url.Parse(upstream.URL)
			gateway := httptest.NewServer(Handler(registry(t), u, http.DefaultTransport))
			defer gateway.Close()
			defer close(release) // release the handler before either server waits in Close
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			done := make(chan error, 1)
			go func() {
				q, _ := http.NewRequestWithContext(ctx, "GET", gateway.URL+"/api/conversations/id/turns", nil)
				res, err := http.DefaultClient.Do(q)
				if err == nil {
					defer res.Body.Close()
					first := make([]byte, len("data: first\n\n"))
					_, err = io.ReadFull(res.Body, first)
					if err == nil && string(first) != "data: first\n\n" {
						err = io.ErrUnexpectedEOF
					}
				}
				done <- err
			}()
			select {
			case <-started:
			case <-ctx.Done():
				t.Fatal("request never reached upstream")
			}
			if streaming {
				if err := <-done; err != nil {
					t.Fatalf("first SSE chunk was not delivered: %v", err)
				}
			} else {
				cancel()
				if err := <-done; err == nil {
					t.Fatal("cancelled request unexpectedly succeeded")
				}
			}
			select {
			case <-stopped:
			case <-time.After(2 * time.Second):
				t.Fatal("upstream work survived client disconnect")
			}
		})
	}
}
