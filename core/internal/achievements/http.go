// SPDX-License-Identifier: AGPL-3.0-or-later
package achievements

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
)

func CharacterID(path string) (string, bool) {
	p := strings.Split(path, "/")
	if len(p) == 5 && p[1] == "api" && p[2] == "characters" && p[3] != "" && p[4] == "achievements" {
		return p[3], true
	}
	return "", false
}

// NewHandler captures one private response on a fresh HTTP/1 connection. It never retries.
func NewHandler(upstream *url.URL, transport http.RoundTripper, token string) http.Handler {
	if base, ok := transport.(*http.Transport); ok {
		dedicated := base.Clone()
		dedicated.DisableKeepAlives = true
		dedicated.ForceAttemptHTTP2 = false
		dedicated.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
		transport = dedicated
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		character, ok := CharacterID(q.URL.Path)
		if !ok || q.Method != "GET" {
			failure(w)
			return
		}
		target := *upstream
		target.Path = "/api/_core/achievements/" + character
		target.RawPath = ""
		target.RawQuery = q.URL.RawQuery
		request, err := http.NewRequestWithContext(q.Context(), "GET", target.String(), nil)
		if err != nil {
			failure(w)
			return
		}
		for _, name := range []string{"Cookie", "Origin", "X-Work-ID"} {
			for _, v := range q.Header.Values(name) {
				request.Header.Add(name, v)
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
		for _, cookie := range response.Header.Values("Set-Cookie") {
			w.Header().Add("Set-Cookie", cookie)
		}
		w.Header().Set("Cache-Control", "no-store")
		if response.StatusCode != 200 {
			data, e := io.ReadAll(io.LimitReader(response.Body, 65537))
			if e == nil && len(data) <= 65536 && strings.Split(response.Header.Get("Content-Type"), ";")[0] == "application/json" {
				envelope, ok := errorObject(data)
				if ok && len(envelope) == 2 && envelope["contract"] == "sc-character-achievements-v1" && envelope["error"] == "legacy_read" && response.StatusCode == 500 {
					legacy(w)
					return
				}
				if ok && len(envelope) == 1 && envelope["detail"] != "" && envelope["detail"] != "Not Found" {
					status := response.StatusCode
					if status == 401 || status == 403 || status == 409 || status == 428 || status == 404 && (envelope["detail"] == "角色不存在" || envelope["detail"] == "用户不存在") {
						w.Header().Set("Content-Type", "application/json")
						w.WriteHeader(status)
						w.Write(data)
						return
					}
				}
			}
			failure(w)
			return
		}
		if strings.Split(response.Header.Get("Content-Type"), ";")[0] != "application/x-ndjson" {
			failure(w)
			return
		}
		snapshot, err := Decode(response.Body, character)
		if err != nil {
			if q.Context().Err() == nil {
				failure(w)
			}
			return
		}
		result, err := Compute(snapshot)
		if err == ErrLegacy {
			legacy(w)
			return
		}
		if err != nil {
			failure(w)
			return
		}
		data, err := json.Marshal(result)
		if err != nil {
			failure(w)
			return
		}
		if q.Context().Err() != nil {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(append(data, '\n'))
	})
}

func errorObject(data []byte) (map[string]string, bool) {
	if !validJSONText(data) {
		return nil, false
	}
	d := json.NewDecoder(bytes.NewReader(data))
	token, err := d.Token()
	if err != nil || token != json.Delim('{') {
		return nil, false
	}
	fields := map[string]string{}
	for d.More() {
		key, err := d.Token()
		if err != nil {
			return nil, false
		}
		k, ok := key.(string)
		if !ok {
			return nil, false
		}
		if _, exists := fields[k]; exists {
			return nil, false
		}
		var v string
		if d.Decode(&v) != nil {
			return nil, false
		}
		fields[k] = v
	}
	if token, err = d.Token(); err != nil || token != json.Delim('}') {
		return nil, false
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return nil, false
	}
	return fields, true
}
func failure(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(502)
	w.Write([]byte("{\"detail\":\"SC achievements unavailable\"}\n"))
}
func legacy(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(500)
	w.Write([]byte("Internal Server Error"))
}
