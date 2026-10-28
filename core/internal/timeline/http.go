// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"crypto/tls"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

func ConversationID(path string) (string, bool) {
	parts := strings.Split(path, "/")
	if len(parts) == 5 && parts[1] == "api" && parts[2] == "conversations" && parts[3] != "" && parts[4] == "messages" {
		return parts[3], true
	}
	return "", false
}

// NewHandler keeps the original GET and its Origin semantics.
func NewHandler(upstream *url.URL, transport http.RoundTripper, token string) http.Handler {
	if original, ok := transport.(*http.Transport); ok {
		// net/http retries idempotent requests after errors on reused connections.
		// A fresh HTTP/1 connection per captured read prevents that hidden second read.
		dedicated := original.Clone()
		dedicated.DisableKeepAlives = true
		dedicated.ForceAttemptHTTP2 = false
		dedicated.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
		transport = dedicated
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		id, ok := ConversationID(q.URL.Path)
		if !ok || q.Method != "GET" {
			failure(w)
			return
		}
		target := *upstream
		target.Path = "/api/_core/conversations/" + id + "/messages"
		target.RawPath = ""
		target.RawQuery = q.URL.RawQuery
		request, err := http.NewRequestWithContext(q.Context(), "GET", target.String(), nil)
		if err != nil {
			failure(w)
			return
		}
		for _, name := range []string{"Cookie", "Origin", "X-Work-ID"} {
			for _, value := range q.Header.Values(name) {
				request.Header.Add(name, value)
			}
		}
		request.Header.Set("Authorization", "Bearer "+token)
		// No application retry or client Idempotency-Key: a second read has a new watermark.
		response, err := client.Do(request)
		if err != nil {
			if q.Context().Err() == nil {
				failure(w)
			}
			return
		}
		defer response.Body.Close()
		w.Header().Set("Cache-Control", "no-store")
		for _, cookie := range response.Header.Values("Set-Cookie") {
			w.Header().Add("Set-Cookie", cookie)
		}
		if response.StatusCode != 200 {
			data, readErr := io.ReadAll(io.LimitReader(response.Body, 4097))
			var detail map[string]string
			if readErr == nil && len(data) <= 4096 && json.Unmarshal(data, &detail) == nil && len(detail) == 1 {
				if response.StatusCode == 500 && detail["error"] == "unknown_gift" {
					w.Header().Set("Content-Type", "text/plain; charset=utf-8")
					w.WriteHeader(500)
					io.WriteString(w, "Internal Server Error")
					return
				}
				message := detail["detail"]
				status := response.StatusCode
				if message != "" && message != "Not Found" && (status == 401 || status == 403 || status == 404 || status == 409 || status == 428) {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					w.Write(data)
					return
				}
			}
			failure(w)
			return
		}
		if strings.Split(response.Header.Get("Content-Type"), ";")[0] != "application/x-ndjson" {
			failure(w)
			return
		}
		public, err := temporary()
		if err != nil {
			failure(w)
			return
		}
		defer public.Close()
		if err = Project(q.Context(), response.Body, public, id); err != nil {
			if q.Context().Err() == nil {
				failure(w)
			}
			return
		}
		length, err := public.Seek(0, io.SeekCurrent)
		if err != nil {
			failure(w)
			return
		}
		if _, err = public.Seek(0, io.SeekStart); err != nil {
			failure(w)
			return
		}
		if q.Context().Err() != nil {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Content-Length", strconv.FormatInt(length, 10))
		io.Copy(w, public)
	})
}

func failure(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(502)
	json.NewEncoder(w).Encode(map[string]string{"detail": "SC history unavailable"})
}
