// SPDX-License-Identifier: AGPL-3.0-or-later
// Package conversationcreate owns SC admission and single-attempt create commands.
// Python authenticates twice and remains the sole transactional SQLite writer.
package conversationcreate

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const privatePath = "/api/_core/sc-conversation-create/"
const unknown = "创建结果暂未确认，请先核对会话列表"

var digestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

type preparation struct {
	Version       int    `json:"version"`
	WorkID        string `json:"work_id"`
	UserID        string `json:"user_id"`
	CharacterID   string `json:"character_id"`
	ContentDigest string `json:"content_digest"`
}
type command struct {
	preparation
	Operation string `json:"operation"`
	CommandID string `json:"command_id"`
}
type conversation struct {
	ID          string  `json:"id"`
	UserID      string  `json:"user_id"`
	CharacterID string  `json:"character_id"`
	CreatedAt   string  `json:"created_at"`
	Title       *string `json:"title"`
}

// NewHandler has its own fresh HTTP/1 write transport: no redirects, connection
// reuse, GetBody, client idempotency headers, retries or legacy fallback.
func NewHandler(upstream *url.URL, token, digest string, characters []string) (http.Handler, error) {
	if upstream.Scheme != "http" || upstream.Host == "" || !digestPattern.MatchString(token) || !digestPattern.MatchString(digest) || len(characters) == 0 {
		return nil, errors.New("invalid SC create configuration")
	}
	members := make(map[string]bool, len(characters))
	for _, id := range characters {
		members[id] = true
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DisableKeepAlives = true
	transport.ForceAttemptHTTP2 = false
	client := &http.Client{Transport: transport, Timeout: 5 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json")
		if q.Method != "POST" || q.URL.Path != "/api/conversations" {
			fail(w, 404, "Not Found")
			return
		}
		body, err := io.ReadAll(http.MaxBytesReader(w, q.Body, 1<<20))
		q.Body.Close()
		if err != nil {
			fail(w, 413, "request too large")
			return
		}
		// Every returned cookie is appended before interpreting body/status, including
		// preparation renewal followed by an admission or execution failure.
		call := func(step string, data []byte) (int, []byte, error) {
			target := *upstream
			target.Path = privatePath + step
			target.RawPath = ""
			target.RawQuery = q.URL.RawQuery
			req, _ := http.NewRequestWithContext(q.Context(), "POST", target.String(), io.NopCloser(bytes.NewReader(data)))
			req.ContentLength = int64(len(data)) // NopCloser prevents NewRequest from installing GetBody.
			for _, name := range []string{"Cookie", "Origin", "X-Work-ID"} {
				for _, v := range q.Header.Values(name) {
					req.Header.Add(name, v)
				}
			}
			req.Header.Set("Authorization", "Bearer "+token)
			if step == "execute" {
				req.Header.Set("Content-Type", "application/json")
			} else {
				for _, v := range q.Header.Values("Content-Type") {
					req.Header.Add("Content-Type", v)
				}
			}
			response, err := client.Do(req)
			if err != nil {
				return 0, nil, err
			}
			defer response.Body.Close()
			for _, cookie := range response.Header.Values("Set-Cookie") {
				w.Header().Add("Set-Cookie", cookie)
			}
			// A prepare reply may echo the original <=1MiB input in a 422 or
			// in character_id. Allow worst-case JSON escaping plus framing.
			limit := 65536
			if step == "prepare" {
				limit = 6*(1<<20) + 65536
			}
			data, err = io.ReadAll(io.LimitReader(response.Body, int64(limit+1)))
			if len(data) > limit {
				err = errors.New("oversized create response")
			}
			return response.StatusCode, data, err
		}
		status, data, err := call("prepare", body)
		if err != nil {
			fail(w, 502, "SC conversation creation unavailable")
			return
		}
		if status != 200 {
			if !publicError(w, status, data, true) {
				fail(w, 502, "SC conversation creation unavailable")
			}
			return
		}
		var prepared preparation
		if !strict(data, &prepared, []string{"version", "work_id", "user_id", "character_id", "content_digest"}) || prepared.Version != 1 || prepared.WorkID != "sc" || prepared.UserID == "" || prepared.ContentDigest != digest {
			fail(w, 502, "SC conversation creation unavailable")
			return
		}
		if !members[prepared.CharacterID] {
			fail(w, 404, "角色不存在")
			return
		}
		id := make([]byte, 16)
		if _, err := rand.Read(id); err != nil {
			fail(w, 502, "SC conversation creation unavailable")
			return
		}
		id[6] = (id[6] & 0x0f) | 0x40
		id[8] = (id[8] & 0x3f) | 0x80
		cmd := command{prepared, "CreateSCConversation", hex.EncodeToString(id)}
		encoded, _ := json.Marshal(cmd)
		// Prepare may complete just as the public client disconnects. Do not submit a
		// command already known to be unwanted. After this point the result can be unknown.
		if q.Context().Err() != nil {
			return
		}
		status, data, err = call("execute", encoded)
		if err != nil {
			fail(w, 502, unknown)
			return
		}
		if status != 201 {
			if !publicError(w, status, data, false) {
				fail(w, 502, unknown)
			}
			return
		}
		var result conversation
		if !strict(data, &result, []string{"id", "user_id", "character_id", "created_at", "title"}) || result.ID != cmd.CommandID || result.UserID != cmd.UserID || result.CharacterID != cmd.CharacterID || result.Title != nil {
			fail(w, 502, unknown)
			return
		}
		// The legacy writer emits Python UTC ISO time; reject missing/invalid timestamps.
		if _, err := time.Parse(time.RFC3339Nano, result.CreatedAt); err != nil || !strings.HasSuffix(result.CreatedAt, "+00:00") {
			fail(w, 502, unknown)
			return
		}
		w.WriteHeader(201)
		json.NewEncoder(w).Encode(result)
	}), nil
}

func strict(data []byte, target any, keys []string) bool {
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil || len(fields) != len(keys) {
		return false
	}
	for _, key := range keys {
		v, ok := fields[key]
		if !ok || (key != "title" && bytes.Equal(bytes.TrimSpace(v), []byte("null"))) {
			return false
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target) == nil
}

func publicError(w http.ResponseWriter, status int, data []byte, prepare bool) bool {
	allowed := status == 401 || status == 403 || status == 428 || (prepare && (status == 400 || status == 409 || status == 422))
	if !allowed {
		return false
	}
	var value map[string]json.RawMessage
	if json.Unmarshal(data, &value) != nil || len(value) != 1 || value["detail"] == nil {
		return false
	}
	if status == 422 && !validationErrors(value["detail"]) {
		return false
	}
	if status != 422 {
		var detail string
		if json.Unmarshal(value["detail"], &detail) != nil || detail == "" || detail == "Not Found" || (status == 400 && detail != "There was an error parsing the body") {
			return false
		}
	}
	w.WriteHeader(status)
	w.Write(data)
	return true
}
func fail(w http.ResponseWriter, status int, detail string) {
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"detail": detail})
}

// Accept only the original NewConversation validation contract. In particular a
// private 422 is not authority to publish arbitrary private response fields.
func validationErrors(data []byte) bool {
	var items []map[string]json.RawMessage
	if json.Unmarshal(data, &items) != nil || len(items) == 0 {
		return false
	}
	for _, item := range items {
		for key := range item {
			if key != "type" && key != "loc" && key != "msg" && key != "input" && key != "ctx" {
				return false
			}
		}
		for _, key := range []string{"type", "loc", "msg", "input"} {
			if _, ok := item[key]; !ok {
				return false
			}
		}
		var kind, message string
		if json.Unmarshal(item["type"], &kind) != nil || json.Unmarshal(item["msg"], &message) != nil || message == "" {
			return false
		}
		switch kind {
		case "missing", "string_type", "model_attributes_type", "json_invalid":
		default:
			return false
		}
		var loc []any
		if json.Unmarshal(item["loc"], &loc) != nil || len(loc) == 0 || len(loc) > 2 || loc[0] != "body" {
			return false
		}
		if len(loc) == 2 {
			switch part := loc[1].(type) {
			case string:
				if part != "character_id" {
					return false
				}
			case float64:
				if kind != "json_invalid" || part < 0 || part != float64(int64(part)) {
					return false
				}
			default:
				return false
			}
		}
		if raw, ok := item["ctx"]; ok {
			var ctx map[string]string
			if kind != "json_invalid" || json.Unmarshal(raw, &ctx) != nil || len(ctx) != 1 || ctx["error"] == "" {
				return false
			}
		}
	}
	return true
}
