// SPDX-License-Identifier: AGPL-3.0-or-later
package catalog

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

func TestPrivateAffinityCannotBeForwarded(t *testing.T) {
	var touched atomic.Bool
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { touched.Store(true); w.Write([]byte(`{"value":5}`)) }))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	handler := Handler(&Registry{}, target, http.DefaultTransport)
	for _, path := range []string{"/api/_core/affinity/mano", "/api/%5fcore/affinity/mano", "/api/_core"} {
		for _, method := range []string{"GET", "POST", "HEAD"} {
			req := httptest.NewRequest(method, path, nil)
			req.Header.Set("Authorization", "Bearer "+strings.Repeat("1", 64))
			result := httptest.NewRecorder()
			handler.ServeHTTP(result, req)
			if result.Code != 404 || touched.Load() {
				t.Fatalf("%s %s reached private upstream: %d %s", method, path, result.Code, result.Body.String())
			}
		}
	}
}
