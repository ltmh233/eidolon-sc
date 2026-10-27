// SPDX-License-Identifier: AGPL-3.0-or-later
package affinity

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

func ReadTokenFile(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return "", errors.New("invalid affinity token file")
	}
	data, err := os.ReadFile(path)
	token := strings.TrimRight(string(data), "\r\n")
	if err != nil || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(token) {
		return "", errors.New("invalid affinity token file")
	}
	return token, nil
}

func CharacterID(path string) (string, bool) {
	parts := strings.Split(path, "/")
	if len(parts) == 5 && parts[1] == "api" && parts[2] == "characters" && parts[3] != "" && parts[4] == "affinity" {
		return parts[3], true
	}
	return "", false
}

// NewHandler computes only the affinity GET; Python authenticates its original Cookie.
func NewHandler(upstream *url.URL, transport http.RoundTripper, token string) http.Handler {
	client := &http.Client{Transport: transport, Timeout: 5 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		character, ok := CharacterID(q.URL.Path)
		if !ok || q.Method != "GET" {
			writeFailure(w, 404)
			return
		}
		target := *upstream
		target.Path = "/api/_core/affinity/" + character
		target.RawPath = ""
		target.RawQuery = q.URL.RawQuery
		request, _ := http.NewRequestWithContext(q.Context(), "GET", target.String(), nil)
		for _, name := range []string{"Cookie", "Origin", "X-Work-ID"} {
			for _, value := range q.Header.Values(name) {
				request.Header.Add(name, value)
			}
		}
		request.Header.Set("Authorization", "Bearer "+token)
		response, err := client.Do(request)
		if err != nil {
			if q.Context().Err() == nil {
				writeFailure(w, 502)
			}
			return
		}
		defer response.Body.Close()
		data, err := io.ReadAll(io.LimitReader(response.Body, 4097))
		if err != nil || len(data) > 4096 {
			writeFailure(w, 502)
			return
		}
		for _, cookie := range response.Header.Values("Set-Cookie") {
			w.Header().Add("Set-Cookie", cookie)
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json")
		if response.StatusCode != 200 {
			switch response.StatusCode {
			case 401, 403, 404, 409, 428:
				var detail map[string]json.RawMessage
				var message string
				if json.Unmarshal(data, &detail) == nil && len(detail) == 1 && string(bytes.TrimSpace(detail["detail"])) != "null" && json.Unmarshal(detail["detail"], &message) == nil && message != "Not Found" {
					w.WriteHeader(response.StatusCode)
					w.Write(data)
					return
				}
			}
			writeFailure(w, 502)
			return
		}
		snapshot, err := decodeSnapshot(data, character)
		if err != nil {
			writeFailure(w, 502)
			return
		}
		result, err := Compute(snapshot)
		if err != nil {
			writeFailure(w, 502)
			return
		}
		json.NewEncoder(w).Encode(result)
	})
}

func decodeSnapshot(data []byte, character string) (Snapshot, error) {
	var snapshot Snapshot
	var raw map[string]json.RawMessage
	if json.Unmarshal(data, &raw) != nil {
		return snapshot, errors.New("invalid affinity contract")
	}
	for _, key := range []string{"version", "work_id", "character_id", "enabled", "observed_at", "state"} {
		if _, ok := raw[key]; !ok {
			return snapshot, errors.New("missing affinity contract field")
		}
	}
	if flag := string(bytes.TrimSpace(raw["enabled"])); flag != "true" && flag != "false" {
		return snapshot, errors.New("invalid affinity flag")
	}
	if string(bytes.TrimSpace(raw["state"])) != "null" {
		var state map[string]json.RawMessage
		if json.Unmarshal(raw["state"], &state) != nil || len(state) != 3 {
			return snapshot, errors.New("invalid affinity state")
		}
		for _, key := range []string{"value", "updated_at_us", "cooling"} {
			if v, ok := state[key]; !ok || string(bytes.TrimSpace(v)) == "null" {
				return snapshot, errors.New("invalid affinity state field")
			}
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&snapshot); err != nil {
		return snapshot, err
	}
	if snapshot.Version != 1 || snapshot.WorkID != "sc" || snapshot.CharacterID != character || (!snapshot.Enabled && snapshot.State != nil) {
		return snapshot, errors.New("unexpected affinity scope")
	}
	if _, err := time.Parse(time.RFC3339Nano, snapshot.ObservedAt); err != nil {
		return snapshot, err
	}
	return snapshot, nil
}

func writeFailure(w http.ResponseWriter, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"detail": "SC affinity unavailable"})
}
