// SPDX-License-Identifier: AGPL-3.0-or-later
package conversationcreate

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

func TestCreateOwnsAdmissionIdentityAndPreservesCookies(t *testing.T) {
	var executes atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+strings.Repeat("2", 64) || r.Header.Get("Origin") != "http://localhost:3000" || r.Header.Get("Cookie") != "session=original" {
			t.Error("lost authenticated original request")
		}
		if strings.HasSuffix(r.URL.Path, "prepare") {
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "renewed", Path: "/"})
			io.WriteString(w, `{"version":1,"work_id":"sc","user_id":"u","character_id":"mano","content_digest":"`+strings.Repeat("a", 64)+`"}`)
		} else {
			executes.Add(1)
			var command map[string]any
			json.NewDecoder(r.Body).Decode(&command)
			if command["operation"] != "CreateSCConversation" || command["user_id"] != "u" || command["work_id"] != "sc" {
				t.Error("unbound command")
			}
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "last", Path: "/"})
			http.SetCookie(w, &http.Cookie{Name: "second", Value: "cookie", Path: "/"})
			w.WriteHeader(201)
			json.NewEncoder(w).Encode(map[string]any{"id": command["command_id"], "user_id": "u", "character_id": "mano", "created_at": "2026-10-28T00:00:00.123456+00:00", "title": nil})
		}
	}))
	defer upstream.Close()
	target, _ := url.Parse(upstream.URL)
	handler, err := NewHandler(target, strings.Repeat("2", 64), strings.Repeat("a", 64), []string{"mano"})
	if err != nil {
		t.Fatal(err)
	}
	q := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"character_id":"mano","user_id":"attacker"}`))
	q.Header.Set("Cookie", "session=original")
	q.Header.Set("Origin", "http://localhost:3000")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, q)
	if w.Code != 201 || executes.Load() != 1 {
		t.Fatalf("status=%d attempts=%d body=%s", w.Code, executes.Load(), w.Body.String())
	}
	cookies := w.Result().Cookies()
	if len(cookies) != 3 || cookies[0].Value != "renewed" || cookies[1].Value != "last" {
		t.Fatalf("cookies=%v", cookies)
	}
}

func TestAdmissionFailureKeepsPreparationCookieAndNeverExecutes(t *testing.T) {
	for _, scenario := range []struct {
		name, character, digest string
		status                  int
	}{
		{"unknown character", "missing", strings.Repeat("a", 64), 404},
		{"mismatched release", "mano", strings.Repeat("b", 64), 502},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			var executed atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "execute") {
					executed.Add(1)
					t.Error("admission failure sent command")
					return
				}
				w.Header().Add("Set-Cookie", "session=renewed; Path=/; HttpOnly")
				json.NewEncoder(w).Encode(preparation{1, "sc", "u", scenario.character, scenario.digest})
			}))
			defer server.Close()
			target, _ := url.Parse(server.URL)
			handler, err := NewHandler(target, strings.Repeat("2", 64), strings.Repeat("a", 64), []string{"mano"})
			if err != nil {
				t.Fatal(err)
			}
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"character_id":"mano"}`)))
			if w.Code != scenario.status || executed.Load() != 0 || len(w.Result().Cookies()) != 1 {
				t.Fatalf("status=%d commands=%d cookies=%v", w.Code, executed.Load(), w.Result().Cookies())
			}
		})
	}
}

func TestCommittedConnectionLossIsSingleAttempt(t *testing.T) {
	var executed atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "prepare") {
			json.NewEncoder(w).Encode(preparation{1, "sc", "u", "mano", strings.Repeat("a", 64)})
			return
		}
		if r.Header.Get("Idempotency-Key") != "" || r.Header.Get("X-User-ID") != "" {
			t.Error("forwarded client authority")
		}
		var cmd command
		if err := json.NewDecoder(r.Body).Decode(&cmd); err != nil {
			t.Error(err)
		}
		executed.Add(1) // Actual server receipt, followed by a transport failure.
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	}))
	defer server.Close()
	target, _ := url.Parse(server.URL)
	handler, _ := NewHandler(target, strings.Repeat("2", 64), strings.Repeat("a", 64), []string{"mano"})
	request := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"character_id":"mano"}`))
	request.Header.Set("Idempotency-Key", "client-key")
	request.Header.Set("X-User-ID", "attacker")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	if w.Code != 502 || executed.Load() != 1 || !strings.Contains(w.Body.String(), unknown) {
		t.Fatalf("status=%d commands=%d body=%s", w.Code, executed.Load(), w.Body.String())
	}
}

func TestInvalidCommittedResultsNeverTriggerASecondCommand(t *testing.T) {
	for _, key := range []string{"id", "user_id", "character_id", "title", "created_at", "extra", "missing", "null-user", "wrong-type", "status", "redirect"} {
		t.Run(key, func(t *testing.T) {
			var executed atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasSuffix(r.URL.Path, "prepare") {
					json.NewEncoder(w).Encode(preparation{1, "sc", "u", "mano", strings.Repeat("a", 64)})
					return
				}
				executed.Add(1)
				var cmd command
				json.NewDecoder(r.Body).Decode(&cmd)
				row := map[string]any{"id": cmd.CommandID, "user_id": "u", "character_id": "mano", "created_at": "2026-10-28T00:00:00+00:00", "title": nil}
				status := 201
				switch key {
				case "missing":
					delete(row, "title")
				case "null-user":
					row["user_id"] = nil
				case "wrong-type":
					row["id"] = 123
				case "status":
					status = 200
				case "redirect":
					status = 307
					w.Header().Set("Location", "/would-replay")
				default:
					row[key] = "wrong"
				}
				w.Header().Add("Set-Cookie", "execution=cookie; Path=/")
				w.WriteHeader(status)
				json.NewEncoder(w).Encode(row)
			}))
			defer server.Close()
			target, _ := url.Parse(server.URL)
			handler, _ := NewHandler(target, strings.Repeat("2", 64), strings.Repeat("a", 64), []string{"mano"})
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"character_id":"mano"}`)))
			if w.Code != 502 || executed.Load() != 1 || len(w.Result().Cookies()) != 1 || !strings.Contains(w.Body.String(), unknown) {
				t.Fatalf("status=%d commands=%d body=%s", w.Code, executed.Load(), w.Body.String())
			}
		})
	}
}

func TestPrepareValidationErrorsCannotExposePrivateFields(t *testing.T) {
	for _, body := range []string{`{"internal_path":"private"}`, `{"detail":"private"}`, `{"detail":[{"type":"missing","loc":["body","character_id"],"msg":"Field required","input":{},"internal_path":"private"}]}`} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(422); io.WriteString(w, body) }))
		target, _ := url.Parse(server.URL)
		handler, _ := NewHandler(target, strings.Repeat("2", 64), strings.Repeat("a", 64), []string{"mano"})
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{}`)))
		server.Close()
		if w.Code != 502 || strings.Contains(w.Body.String(), "private") {
			t.Fatalf("contract=%s status=%d result=%s", body, w.Code, w.Body.String())
		}
	}
}

func TestOnlyOriginalBodyParse400IsPublic(t *testing.T) {
	for _, scenario := range []struct {
		body   string
		status int
	}{
		{`{"detail":"There was an error parsing the body"}`, 400},
		{`{"detail":"internal path private"}`, 502},
		{`{"internal_path":"private"}`, 502},
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(400); io.WriteString(w, scenario.body) }))
		target, _ := url.Parse(server.URL)
		handler, _ := NewHandler(target, strings.Repeat("2", 64), strings.Repeat("a", 64), []string{"mano"})
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{}`)))
		server.Close()
		if w.Code != scenario.status || strings.Contains(w.Body.String(), "private") {
			t.Fatalf("contract=%s status=%d body=%s", scenario.body, w.Code, w.Body.String())
		}
	}
}
