// SPDX-License-Identifier: AGPL-3.0-or-later
package stats

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
)

// NewHandler has no whole-response deadline or history-size cap. The downstream
// context cancels the request; Compute bounds individual records and retained state.
func NewHandler(upstream *url.URL, transport http.RoundTripper, token string) http.Handler {
	client := &http.Client{Transport: transport, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		target := *upstream
		target.Path = "/api/_core/stats"
		target.RawPath = ""
		target.RawQuery = q.URL.RawQuery
		request, err := http.NewRequestWithContext(q.Context(), "GET", target.String(), nil)
		if err != nil {
			failure(w)
			return
		}
		for _, name := range []string{"Cookie", "Origin", "X-Work-ID", "X-Timezone"} {
			for _, value := range q.Header.Values(name) {
				request.Header.Add(name, value)
			}
		}
		request.Header.Set("Authorization", "Bearer "+token)
		response, err := client.Do(request)
		if err != nil {
			if q.Context().Err() == nil {
				failure(w)
			}
			return
		}
		defer response.Body.Close()
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json")
		for _, cookie := range response.Header.Values("Set-Cookie") {
			w.Header().Add("Set-Cookie", cookie)
		}
		if response.StatusCode != 200 {
			// Validation errors are the original public FastAPI contract, including detail arrays.
			// They are bounded independently from the arbitrarily long successful history stream.
			data, readErr := io.ReadAll(io.LimitReader(response.Body, maxRecord+1))
			var detail map[string]json.RawMessage
			if readErr == nil && len(data) <= maxRecord && json.Unmarshal(data, &detail) == nil && len(detail) == 1 {
				var message string
				scalar := json.Unmarshal(detail["detail"], &message) == nil && message != "" && message != "Not Found"
				validation := response.StatusCode == 422 && validValidation(detail["detail"])
				status := response.StatusCode
				if (scalar && (status == 401 || status == 403 || status == 404 || status == 409 || status == 428)) || validation {
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
		result, err := Compute(response.Body)
		if err != nil {
			if q.Context().Err() == nil {
				failure(w)
			}
			return
		}
		json.NewEncoder(w).Encode(result)
	})
}

func validValidation(data json.RawMessage) bool {
	var shape []map[string]json.RawMessage
	if json.Unmarshal(data, &shape) != nil || len(shape) != 1 || len(shape[0]) != 5 {
		return false
	}
	for _, key := range []string{"type", "loc", "msg", "input", "ctx"} {
		if value, ok := shape[0][key]; !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return false
		}
	}
	var problems []struct {
		Type  string   `json:"type"`
		Loc   []string `json:"loc"`
		Msg   string   `json:"msg"`
		Input string   `json:"input"`
		Ctx   struct {
			Expected string `json:"expected"`
		} `json:"ctx"`
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&problems) != nil {
		return false
	}
	p := problems[0]
	return p.Type == "literal_error" && len(p.Loc) == 2 && p.Loc[0] == "query" && p.Loc[1] == "range" && p.Msg != "" && p.Ctx.Expected != ""
}

func failure(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(502)
	json.NewEncoder(w).Encode(map[string]string{"detail": "SC statistics unavailable"})
}
