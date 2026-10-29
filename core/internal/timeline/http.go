// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
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

type readTarget struct {
	path, identity   string
	missingCharacter bool
	comparisonKeys   bool
}
type readProjection func(context.Context, io.Reader, io.Writer, string) error

// NewHandler keeps the original GET and its Origin semantics.
func NewHandler(upstream *url.URL, transport http.RoundTripper, token string) http.Handler {
	return readHandler(upstream, transport, token, func(q *http.Request) (readTarget, bool) {
		id, ok := ConversationID(q.URL.Path)
		return readTarget{path: "/api/_core/conversations/" + id + "/messages", identity: id, comparisonKeys: true}, ok && q.Method == "GET"
	}, projectPythonTimeline)
}

func NewSummaryHandler(upstream *url.URL, transport http.RoundTripper, token string) http.Handler {
	return readHandler(upstream, transport, token, func(q *http.Request) (readTarget, bool) {
		if q.Method != "GET" {
			return readTarget{}, false
		}
		switch q.URL.Path {
		case "/api/conversations":
			return readTarget{path: "/api/_core/conversation-summaries", identity: "list", missingCharacter: true}, true
		case "/api/conversations/latest":
			return readTarget{path: "/api/_core/conversation-summaries/latest", identity: "latest"}, true
		}
		return readTarget{}, false
	}, projectPythonSummaries)
}

func readHandler(upstream *url.URL, transport http.RoundTripper, token string, resolve func(*http.Request) (readTarget, bool), project readProjection) http.Handler {
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
		destination, ok := resolve(q)
		if !ok {
			failure(w)
			return
		}
		target := *upstream
		target.Path = destination.path
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
		request.Header.Set(displayHeader, displayContract)
		if destination.comparisonKeys {
			request.Header.Set("X-Core-History-Comparison", "python-us-v1")
		}
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
			if readErr == nil && response.StatusCode == 422 && len(data) <= 4096 && destination.missingCharacter && validMissingCharacter(data) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(422)
				w.Write(data)
				return
			}
			if readErr == nil && len(data) <= 4096 && json.Unmarshal(data, &detail) == nil && len(detail) == 1 {
				if response.StatusCode == 500 && detail["error"] == "unknown_gift" {
					w.Header().Set("Content-Type", "text/plain; charset=utf-8")
					w.WriteHeader(500)
					io.WriteString(w, "Internal Server Error")
					return
				}
				message := detail["detail"]
				status := response.StatusCode
				if message != "" && message != "Not Found" && message != "unsupported display text contract" && (status == 401 || status == 403 || status == 404 || status == 409 || status == 428) {
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
		if values := response.Header.Values(displayHeader); len(values) != 1 || values[0] != displayContract {
			failure(w)
			return
		}
		if destination.comparisonKeys && response.Header.Get("X-Core-History-Comparison") != "python-us-v1" {
			failure(w)
			return
		}
		public, err := temporary()
		if err != nil {
			failure(w)
			return
		}
		defer public.Close()
		if err = project(q.Context(), response.Body, public, destination.identity); err != nil {
			if errors.Is(err, unknownGift) || errors.Is(err, legacyPreviewFailure) {
				w.Header().Set("Content-Type", "text/plain; charset=utf-8")
				w.WriteHeader(500)
				io.WriteString(w, "Internal Server Error")
				return
			}
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

func validMissingCharacter(data []byte) bool {
	var outer map[string]json.RawMessage
	if json.Unmarshal(data, &outer) != nil || len(outer) != 1 {
		return false
	}
	var problems []map[string]json.RawMessage
	if json.Unmarshal(outer["detail"], &problems) != nil || len(problems) != 1 || len(problems[0]) != 4 {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(outer["detail"]))
	decoder.DisallowUnknownFields()
	var all []struct {
		Type  string          `json:"type"`
		Loc   []string        `json:"loc"`
		Msg   string          `json:"msg"`
		Input json.RawMessage `json:"input"`
	}
	if decoder.Decode(&all) != nil || len(all) != 1 {
		return false
	}
	problem := all[0]
	return problem.Type == "missing" && len(problem.Loc) == 2 && problem.Loc[0] == "query" && problem.Loc[1] == "character_id" && problem.Msg == "Field required" && bytes.Equal(bytes.TrimSpace(problem.Input), []byte("null"))
}

func failure(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(502)
	json.NewEncoder(w).Encode(map[string]string{"detail": "SC history unavailable"})
}
