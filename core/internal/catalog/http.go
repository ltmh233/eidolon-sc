// SPDX-License-Identifier: AGPL-3.0-or-later
package catalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"eidolon/core/internal/affinity"
	"eidolon/core/internal/timeline"
)

// Handler adds public catalog reads, proxying legacy endpoints without rewriting
// bodies, cookies, Origin, redirects or streaming responses. Python owns auth.
func Handler(r *Registry, upstream *url.URL, transport http.RoundTripper, readModels ...http.Handler) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	proxy.Transport = transport
	proxy.FlushInterval = -1
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, _ error) { failure(w, 502, "legacy backend unavailable") }
	client := &http.Client{Transport: transport, Timeout: 5 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		if q.URL.Path == "/api/_core" || strings.HasPrefix(q.URL.Path, "/api/_core/") {
			failure(w, 404, "Not Found")
			return
		}
		if q.URL.Path != "/api/works" && !strings.HasPrefix(q.URL.Path, "/api/works/") {
			// Old endpoints are exclusively SC. Never interpret a supplied DLC scope as SC.
			for _, scope := range append(q.URL.Query()["work_id"], q.Header.Values("X-Work-ID")...) {
				if scope != "sc" {
					failure(w, 409, "legacy API supports sc only; use /api/works/{work_id}")
					return
				}
			}
			// Optional native reads: affinity, statistics, history, then summaries. Existing callers
			// without these handlers retain the explicit legacy proxy mode.
			if (q.URL.Path == "/api/conversations" || q.URL.Path == "/api/conversations/latest") && q.Method == "GET" && len(readModels) > 3 && readModels[3] != nil {
				readModels[3].ServeHTTP(w, q)
				return
			}
			if _, ok := timeline.ConversationID(q.URL.Path); ok && q.Method == "GET" && len(readModels) > 2 && readModels[2] != nil {
				readModels[2].ServeHTTP(w, q)
				return
			}
			if q.URL.Path == "/api/stats" && q.Method == "GET" && len(readModels) > 1 && readModels[1] != nil {
				readModels[1].ServeHTTP(w, q)
				return
			}
			if _, ok := affinity.CharacterID(q.URL.Path); ok && q.Method == "GET" && len(readModels) > 0 && readModels[0] != nil {
				readModels[0].ServeHTTP(w, q)
				return
			}
			// These legacy creation models ignore unknown JSON fields. Refuse an
			// explicit DLC scope before it can accidentally create an SC conversation.
			if q.Method == "POST" && (q.URL.Path == "/api/conversations" || q.URL.Path == "/api/offer") {
				body, err := io.ReadAll(http.MaxBytesReader(w, q.Body, 1<<20))
				if err != nil {
					failure(w, 413, "request too large")
					return
				}
				q.Body.Close()
				q.Body = io.NopCloser(bytes.NewReader(body))
				var payload map[string]json.RawMessage
				if json.Unmarshal(body, &payload) == nil {
					if !legacyScope(payload) {
						failure(w, 409, "legacy API supports sc only")
						return
					}
					var data map[string]json.RawMessage
					if json.Unmarshal(payload["requestData"], &data) == nil && !legacyScope(data) {
						failure(w, 409, "legacy API supports sc only")
						return
					}
				}
			}
			if q.Method == "POST" && q.URL.Path == "/api/conversations" && len(readModels) > 4 && readModels[4] != nil {
				readModels[4].ServeHTTP(w, q)
				return
			}
			// ReverseProxy uploads q.Body while copying the response. An early backend
			// rejection must not let net/http's HTTP/1 response flush close that live
			// body behind Transport's reader. HTTP/2 is already duplex; recorders may
			// not implement this controller operation.
			_ = http.NewResponseController(w).EnableFullDuplex()
			proxy.ServeHTTP(w, q)
			return
		}
		if q.Method != "GET" {
			w.Header().Set("Allow", "GET")
			failure(w, 405, "catalog is read-only")
			return
		}
		if q.URL.RawQuery != "" || q.Header.Get("X-Work-ID") != "" {
			failure(w, 400, "work scope belongs in the path")
			return
		}
		if q.URL.Path == "/api/works" {
			respond(w, map[string]any{"default_work_id": "sc", "works": r.descriptions()})
			return
		}
		parts := strings.Split(strings.TrimPrefix(q.URL.Path, "/api/works/"), "/")
		if len(parts) != 2 {
			failure(w, 404, "unknown catalog route")
			return
		}
		work, ok := r.works[parts[0]]
		if !ok {
			failure(w, 404, "unknown work")
			return
		}
		switch parts[1] {
		case "static-characters":
			listed, ok := r.staticCharacters[work.ID]
			if !ok {
				failure(w, 501, "static content snapshot not configured")
				return
			}
			respond(w, map[string]any{"work_id": work.ID, "characters": listed})
		case "world":
			if work.Source == "legacy-sc" {
				failure(w, 501, "SC world remains internal to Python")
				return
			}
			respond(w, map[string]any{"work_id": work.ID, "setting": work.World})
		case "characters":
			if work.ID == "sc" && r.displayCharacters != nil {
				w.Header().Set("X-Content-ID", r.contentDigest)
				respond(w, map[string]any{"work_id": "sc", "characters": r.displayCharacters})
				return
			}
			result := make([]map[string]any, 0, len(work.Characters))
			if work.Source == "legacy-sc" {
				target := *upstream
				target.Path = "/api/characters"
				target.RawQuery = ""
				req, _ := http.NewRequestWithContext(q.Context(), "GET", target.String(), nil)
				res, err := client.Do(req)
				if err != nil {
					failure(w, 502, "SC catalog unavailable")
					return
				}
				defer res.Body.Close()
				data, err := io.ReadAll(io.LimitReader(res.Body, maxPackage+1))
				var legacy []map[string]any
				if err != nil || res.StatusCode != 200 || len(data) > maxPackage || json.Unmarshal(data, &legacy) != nil || legacy == nil {
					failure(w, 502, "invalid SC catalog response")
					return
				}
				seen := map[string]bool{}
				for _, c := range legacy {
					id, ok := c["id"].(string)
					if !ok || !identifier.MatchString(id) || seen[id] {
						failure(w, 502, "invalid SC character identity")
						return
					}
					seen[id] = true
					result = append(result, entry(work.ID, id, c))
				}
			} else {
				for _, c := range work.Characters {
					result = append(result, entry(work.ID, c.ID, map[string]any{"id": c.ID, "name": c.Name, "intro": c.Intro}))
				}
			}
			respond(w, map[string]any{"work_id": work.ID, "characters": result})
		default:
			failure(w, 501, "work-scoped runtime (including RAG and memory) is not enabled")
		}
	})
}

func legacyScope(payload map[string]json.RawMessage) bool {
	value, exists := payload["work_id"]
	if !exists {
		return true
	}
	var scope string
	return json.Unmarshal(value, &scope) == nil && scope == "sc"
}

func entry(work, id string, display map[string]any) map[string]any {
	return map[string]any{"id": fmt.Sprintf("%s:%s", work, id), "work_id": work, "local_id": id, "display": display}
}
func respond(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(v)
}
func failure(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"detail": message})
}
