// SPDX-License-Identifier: AGPL-3.0-or-later
// Package giftspanel computes the complete SC panel through one authenticated dialog.
package giftspanel

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"eidolon/core/internal/achievements"
)

const contract = "sc-gifts-panel-v1"

var invalid = achievements.ErrContract
var legacy = achievements.ErrLegacy
var noncePattern = regexp.MustCompile(`^[0-9a-f]{32}$`)

type dialog struct {
	r     *bufio.Reader
	w     *io.PipeWriter
	phase int
}

func (d *dialog) write(row ...any) error {
	b, e := json.Marshal(row)
	if e != nil {
		return invalid
	}
	_, e = d.w.Write(append(b, '\n'))
	return e
}
func decode(line []byte) ([]any, error) {
	if !achievements.ValidJSON(line) {
		return nil, invalid
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.UseNumber()
	var row []any
	if decoder.Decode(&row) != nil || row == nil {
		return nil, invalid
	}
	var tail any
	if decoder.Decode(&tail) != io.EOF {
		return nil, invalid
	}
	return row, nil
}
func (d *dialog) row() ([]any, error) {
	line, e := d.r.ReadBytes('\n')
	if e != nil {
		return nil, invalid
	}
	return decode(line)
}
func (d *dialog) end() error {
	row, e := d.row()
	if e != nil || len(row) != 1 || row[0] != "end" {
		return invalid
	}
	if _, e = d.r.ReadByte(); e != io.EOF {
		return invalid
	}
	return nil
}
func (d *dialog) head() ([]any, error) {
	row, e := d.row()
	if e != nil {
		return nil, e
	}
	if len(row) == 0 || row[0] != "error" {
		return row, nil
	}
	// A terminal server error must also close our still-open request writer.
	d.w.Close()
	if len(row) != 3 {
		return nil, invalid
	}
	phase, e := achievements.Size(row[1])
	code, ok := row[2].(string)
	if e != nil || !ok || phase != int64(d.phase) || phase > 3 {
		return nil, invalid
	}
	valid := code == "protocol" || code == "timeout" || code == "internal" || code == "legacy" && phase > 0 || code == "unsupported" && (phase == 1 || phase == 2)
	if !valid || d.end() != nil {
		return nil, invalid
	}
	if code == "legacy" {
		return nil, legacy
	}
	return nil, invalid
}
func challenge(v any) (string, error) {
	s, ok := v.(string)
	if !ok || !noncePattern.MatchString(s) {
		return "", invalid
	}
	return s, nil
}
func profile(v any, enabled bool) (int64, error) {
	if !enabled {
		if v == nil {
			return 0, nil
		}
		return 0, invalid
	}
	p, ok := v.([]any)
	if !ok || len(p) != 2 || p[0] != "py311-sql-v1" {
		return 0, invalid
	}
	n, e := achievements.Size(p[1])
	if e != nil || n != 0 && n < 640 {
		return 0, invalid
	}
	return n, nil
}
func (d *dialog) ready(phase int, nonce string) error {
	r, e := d.row()
	if e != nil || len(r) != 3 || r[0] != "ready" || r[1] != json.Number(strconv.Itoa(phase)) || r[2] != nonce {
		return invalid
	}
	return nil
}
func (d *dialog) abort(phase int, nonce string) error {
	if d.write("abort", phase, nonce, "legacy") != nil {
		return invalid
	}
	d.w.Close()
	if d.end() != nil {
		return invalid
	}
	return legacy
}

type task struct {
	id, selector string
	goal, draws  *big.Int
	progress     achievements.Scalar
}
type daily struct {
	enabled       bool
	nonce, renews string
	tasks         []task
}

func (d *dialog) daily() (daily, error) {
	s := daily{}
	d.phase = 1
	r, e := d.head()
	if e != nil {
		return s, e
	}
	if len(r) != 6 || r[0] != "daily" || r[1] != json.Number("1") {
		return s, invalid
	}
	s.nonce, e = challenge(r[2])
	if e != nil {
		return s, e
	}
	enabled, ok := r[3].(bool)
	if !ok {
		return s, invalid
	}
	s.enabled = enabled
	n, e := achievements.Size(r[4])
	if e != nil {
		return s, e
	}
	digits, e := profile(r[5], enabled)
	if e != nil || !enabled && n != 0 {
		return s, invalid
	}
	if enabled {
		r, e = d.row()
		if e != nil || len(r) != 2 || r[0] != "daily-time" {
			return s, invalid
		}
		s.renews, ok = r[1].(string)
		if !ok {
			return s, invalid
		}
		// next_day emits a canonical UTC datetime (never fractional seconds).
		at, e := time.Parse("2006-01-02T15:04:05+00:00", s.renews)
		if e != nil || at.Format("2006-01-02T15:04:05+00:00") != s.renews {
			return s, invalid
		}
		r, e = d.row()
		if e != nil || len(r) != 3 || r[0] != "daily-counts" {
			return s, invalid
		}
		turns, e1 := achievements.DecodeScalar(r[1], false)
		gifts, e2 := achievements.DecodeScalar(r[2], false)
		if e1 != nil || e2 != nil {
			return s, invalid
		}
		for range n {
			r, e = d.row()
			if e != nil || len(r) != 5 || r[0] != "task" {
				return s, invalid
			}
			id, a := r[1].(string)
			selector, b := r[2].(string)
			goal, c := achievements.RuleInteger(r[3], digits)
			draws, f := achievements.RuleInteger(r[4], digits)
			if !a || !b || selector != "turns" && selector != "gifts" || c != nil || f != nil {
				return s, invalid
			}
			value := turns
			if selector == "gifts" {
				value = gifts
			}
			s.tasks = append(s.tasks, task{id, selector, goal, draws, value})
		}
	}
	if d.ready(1, s.nonce) != nil {
		return s, invalid
	}
	for i := range s.tasks {
		t := &s.tasks[i]
		order, ordered, e := t.progress.CompareInteger(t.goal)
		if e != nil {
			return s, d.abort(1, s.nonce)
		}
		if ordered && order > 0 {
			t.progress = achievements.IntegerScalar(t.goal)
		}
	}
	return s, nil
}

type pair struct{ achievement, character string }
type all struct {
	enabled     bool
	nonce       string
	digits      int64
	tiers       []string
	definitions []achievements.Definition
	counts      map[pair]achievements.Scalar
	stages      map[string]map[string]achievements.Time
	progress    []achievements.Scalar
}

func (d *dialog) all() (all, error) {
	s := all{counts: map[pair]achievements.Scalar{}, stages: map[string]map[string]achievements.Time{}}
	d.phase = 2
	r, e := d.head()
	if e != nil {
		return s, e
	}
	if len(r) != 8 || r[0] != "all" || r[1] != json.Number("2") {
		return s, invalid
	}
	s.nonce, e = challenge(r[2])
	if e != nil {
		return s, e
	}
	enabled, ok := r[3].(bool)
	if !ok {
		return s, invalid
	}
	s.enabled = enabled
	n, e1 := achievements.Size(r[4])
	nc, e2 := achievements.Size(r[5])
	ns, e3 := achievements.Size(r[6])
	s.digits, e = profile(r[7], enabled)
	if e != nil || e1 != nil || e2 != nil || e3 != nil || !enabled && (n != 0 || nc != 0 || ns != 0) {
		return s, invalid
	}
	if enabled {
		r, e = d.row()
		if e != nil || len(r) != 2 || r[0] != "tiers" {
			return s, invalid
		}
		names, ok := r[1].([]any)
		if !ok {
			return s, invalid
		}
		for _, name := range names {
			v, ok := name.(string)
			if !ok {
				return s, invalid
			}
			s.tiers = append(s.tiers, v)
		}
		for range n {
			r, e = d.row()
			if e != nil || len(r) != 3 || r[0] != "achievement" {
				return s, invalid
			}
			id, ok := r[1].(string)
			if !ok {
				return s, invalid
			}
			stages, ok := r[2].([]any)
			if !ok {
				return s, invalid
			}
			def := achievements.Definition{ID: id, Stages: []achievements.Stage{}}
			for _, v := range stages {
				p, ok := v.([]any)
				if !ok || len(p) != 2 {
					return s, invalid
				}
				goal, e1 := achievements.RuleInteger(p[0], s.digits)
				draws, e2 := achievements.RuleInteger(p[1], s.digits)
				if e1 != nil || e2 != nil {
					return s, invalid
				}
				def.Stages = append(def.Stages, achievements.Stage{Goal: goal, Draws: draws})
			}
			s.definitions = append(s.definitions, def)
		}
		for range nc {
			r, e = d.row()
			if e != nil || len(r) != 4 || r[0] != "count" {
				return s, invalid
			}
			a, e1 := achievements.DecodeScalar(r[1], true)
			c, e2 := achievements.DecodeScalar(r[2], true)
			v, e3 := achievements.DecodeScalar(r[3], false)
			if e1 != nil || e2 != nil || e3 != nil {
				return s, invalid
			}
			key := pair{a.Identity(), c.Identity()}
			if _, exists := s.counts[key]; exists {
				return s, invalid
			}
			s.counts[key] = v
		}
		for i := int64(0); i < ns; i++ {
			r, e = d.row()
			if e != nil || len(r) != 4 || r[0] != "stage" {
				return s, invalid
			}
			a, e1 := achievements.DecodeScalar(r[1], true)
			index, e2 := achievements.DecodeScalar(r[2], false)
			at, e3 := achievements.DecodeTime(r[3])
			if e1 != nil || e2 != nil || e3 != nil {
				return s, invalid
			}
			key := a.Identity()
			if s.stages[key] == nil {
				s.stages[key] = map[string]achievements.Time{}
			}
			stage := index.Identity()
			if index.Kind() == "f" && math.IsNaN(index.Float()) {
				stage += ":" + strconv.FormatInt(i, 10)
			}
			if _, exists := s.stages[key][stage]; exists {
				return s, invalid
			}
			s.stages[key][stage] = at
		}
	}
	if d.ready(2, s.nonce) != nil {
		return s, invalid
	}
	for _, def := range s.definitions {
		v, ok := s.counts[pair{"s:" + def.ID, "s:"}]
		if !ok {
			v = achievements.IntegerScalar(big.NewInt(0))
		}
		if def.ID == "characters" || def.ID == "intimate_characters" {
			name, threshold := "s:turns", int64(1)
			if def.ID == "intimate_characters" {
				name, threshold = "s:tier", 0
				for i, tier := range s.tiers {
					if tier == "intimate" {
						threshold = int64(i + 1)
						break
					}
				}
				if threshold == 0 {
					return s, d.abort(2, s.nonce)
				}
			}
			count := big.NewInt(0)
			for key, value := range s.counts {
				if key.achievement != name || key.character == "s:" {
					continue
				}
				order, ordered, e := value.CompareInteger(big.NewInt(threshold))
				if e != nil {
					return s, d.abort(2, s.nonce)
				}
				if ordered && order >= 0 {
					count.Add(count, big.NewInt(1))
				}
			}
			v = achievements.IntegerScalar(count)
		}
		s.progress = append(s.progress, v)
	}
	return s, nil
}

func (s daily) shown() (any, error) {
	if !s.enabled {
		return nil, nil
	}
	tasks := make([]map[string]any, 0, len(s.tasks))
	for _, t := range s.tasks {
		order, ordered, e := t.progress.CompareInteger(t.goal)
		if e != nil {
			return nil, legacy
		}
		var value any = json.Number(t.progress.Value())
		if t.progress.Kind() == "f" {
			f := t.progress.Float()
			if math.IsInf(f, 0) || math.IsNaN(f) {
				// The original typed-dict FastAPI response uses Pydantic JSON's
				// non-finite -> null policy, after all progress has been shown.
				value = nil
			} else {
				text := strconv.FormatFloat(f, 'g', -1, 64)
				if !strings.ContainsAny(text, ".eE") {
					text += ".0"
				}
				value = json.Number(text)
			}
		}
		tasks = append(tasks, map[string]any{"id": t.id, "done": ordered && order >= 0, "progress": value, "goal": t.goal, "draws": t.draws})
	}
	return map[string]any{"tasks": tasks, "renews_at": s.renews}, nil
}
func (s all) shown() (any, error) {
	if !s.enabled {
		return nil, nil
	}
	result := make([]achievements.Progress, 0, len(s.definitions))
	for j, def := range s.definitions {
		n, e := s.progress[j].PythonInt(s.digits)
		if e != nil {
			return nil, legacy
		}
		var progress any = n
		if def.ID == "tier" {
			progress, e = achievements.Tier(n, s.tiers)
			if e != nil {
				return nil, legacy
			}
		}
		p := achievements.Progress{ID: def.ID, Progress: progress, Stages: []achievements.ShownStage{}}
		for i, stage := range def.Stages {
			var goal any = stage.Goal
			if def.ID == "tier" {
				goal, e = achievements.Tier(stage.Goal, s.tiers)
				if e != nil {
					return nil, legacy
				}
			}
			var iso *string
			if at, ok := s.stages["s:"+def.ID]["n:"+strconv.Itoa(i+1)]; ok {
				iso = &at.ISO
			}
			p.Stages = append(p.Stages, achievements.ShownStage{Goal: goal, Draws: stage.Draws, UnlockedAt: iso})
		}
		result = append(result, p)
	}
	return result, nil
}

// object keeps original JSON values exact, while rejecting duplicate or extra base keys.
func object(raw []byte, keys []string) (map[string]json.RawMessage, error) {
	if !achievements.ValidJSON(raw) {
		return nil, invalid
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	tok, e := d.Token()
	if e != nil || tok != json.Delim('{') {
		return nil, invalid
	}
	fields := map[string]json.RawMessage{}
	for d.More() {
		tok, e = d.Token()
		if e != nil {
			return nil, invalid
		}
		key, ok := tok.(string)
		if !ok {
			return nil, invalid
		}
		if _, ok := fields[key]; ok {
			return nil, invalid
		}
		var v json.RawMessage
		if d.Decode(&v) != nil {
			return nil, invalid
		}
		fields[key] = v
	}
	if tok, e = d.Token(); e != nil || tok != json.Delim('}') {
		return nil, invalid
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return nil, invalid
	}
	if len(fields) != len(keys) {
		return nil, invalid
	}
	for _, key := range keys {
		if _, ok := fields[key]; !ok {
			return nil, invalid
		}
	}
	return fields, nil
}
func (d *dialog) state(daily daily, all all) ([]byte, error) {
	d.phase = 3
	// Keep the base object's numeric lexemes; decoding the envelope through interface{}
	// would allow encoding/json to replace duplicate keys and round large integers.
	line, e := d.r.ReadBytes('\n')
	if e != nil {
		return nil, invalid
	}
	row, e := decode(line)
	if e != nil {
		return nil, e
	}
	if len(row) > 0 && row[0] == "error" {
		d.r = bufio.NewReader(io.MultiReader(bytes.NewReader(line), d.r))
		_, e = d.head()
		return nil, e
	}
	if len(row) != 4 || row[0] != "state" || row[1] != json.Number("3") {
		return nil, invalid
	}
	nonce, e := challenge(row[2])
	if e != nil {
		return nil, e
	}
	var parts []json.RawMessage
	if json.Unmarshal(line, &parts) != nil {
		return nil, invalid
	}
	base, e := object(parts[3], []string{"open", "draws", "max_draws", "backpack", "reset_cards", "next_draw_at", "resettable", "characters", "rewarded_at"})
	if e != nil {
		return nil, e
	}
	dailyShown, e1 := daily.shown()
	allShown, e2 := all.shown()
	status := "ok"
	if e1 != nil || e2 != nil {
		status = "legacy"
	} else {
		base["daily_tasks"], e1 = json.Marshal(dailyShown)
		base["achievements"], e2 = json.Marshal(allShown)
		if e1 != nil || e2 != nil {
			status = "legacy"
		}
	}
	var data []byte
	if status == "ok" {
		data, e = json.Marshal(base)
		if e != nil {
			status = "legacy"
		}
	}
	if d.write("finish", 3, nonce, status) != nil {
		return nil, invalid
	}
	d.w.Close()
	if d.end() != nil {
		return nil, invalid
	}
	if status == "legacy" {
		return nil, legacy
	}
	return data, nil
}

func failure(w http.ResponseWriter, e error) {
	w.Header().Set("Cache-Control", "no-store")
	if errors.Is(e, legacy) {
		w.Header().Del("Set-Cookie")
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(500)
		_, _ = w.Write([]byte("Internal Server Error"))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(502)
	_, _ = w.Write([]byte("{\"detail\":\"SC gifts panel unavailable\"}\n"))
}

// NewHandler never retries, probes or resumes; each call opens a fresh HTTP/1 dialog.
func NewHandler(upstream *url.URL, source *http.Transport, token string) http.Handler {
	transport := source.Clone()
	transport.Proxy = nil
	transport.DisableKeepAlives = true
	transport.ForceAttemptHTTP2 = false
	transport.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
	if transport.TLSClientConfig == nil {
		transport.TLSClientConfig = &tls.Config{}
	}
	transport.TLSClientConfig.NextProtos = []string{"http/1.1"}
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		ctx, cancel := context.WithTimeout(q.Context(), 60*time.Second)
		reader, writer := io.Pipe()
		var response *http.Response
		// net/http may wait for its upload loop before returning a canceled Do.
		// Close the pipe independently of Do's return, including before headers.
		stop, stopped := make(chan struct{}), make(chan struct{})
		go func() {
			defer close(stopped)
			select {
			case <-ctx.Done():
				reader.CloseWithError(ctx.Err())
				writer.CloseWithError(ctx.Err())
			case <-stop:
			}
		}()
		defer func() {
			// Stop upload before closing a response that may itself wait for it.
			cancel()
			reader.CloseWithError(context.Canceled)
			writer.CloseWithError(context.Canceled)
			if response != nil {
				response.Body.Close()
			}
			close(stop)
			<-stopped
		}()
		target := *upstream
		target.Path = "/api/_core/sc-gifts-panel"
		target.RawQuery = q.URL.RawQuery
		request, e := http.NewRequestWithContext(ctx, "GET", target.String(), reader)
		if e != nil {
			failure(w, invalid)
			return
		}
		request.Close = true
		request.Header.Set("Authorization", "Bearer "+token)
		request.Header.Set("Content-Type", "application/x-ndjson")
		for _, name := range []string{"Cookie", "Origin", "X-Work-ID"} {
			for _, value := range q.Header.Values(name) {
				request.Header.Add(name, value)
			}
		}
		started := make(chan error, 1)
		go func() { _, e := writer.Write([]byte("[\"begin\",\"" + contract + "\"]\n")); started <- e }()
		response, e = client.Do(request)
		if e != nil {
			cancel()
			reader.CloseWithError(e)
			writer.CloseWithError(e)
			<-started
			failure(w, invalid)
			return
		}
		for _, cookie := range response.Header.Values("Set-Cookie") {
			w.Header().Add("Set-Cookie", cookie)
		}
		if response.StatusCode != 200 {
			writer.Close()
			<-started
			data, e := io.ReadAll(io.LimitReader(response.Body, 65537))
			fields, err := object(data, []string{"detail"})
			status := response.StatusCode
			var detail string
			if e == nil && len(data) <= 65536 && err == nil && json.Unmarshal(fields["detail"], &detail) == nil && detail != "" && strings.Split(response.Header.Get("Content-Type"), ";")[0] == "application/json" && (status == 401 || status == 403 || status == 409 || status == 428) {
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Cache-Control", "no-store")
				w.WriteHeader(status)
				w.Write(data)
				return
			}
			failure(w, invalid)
			return
		}
		if <-started != nil || response.ProtoMajor != 1 || response.ProtoMinor != 1 || strings.Split(response.Header.Get("Content-Type"), ";")[0] != "application/x-ndjson" || response.Header.Get("Cache-Control") != "no-store" || !response.Close || response.ContentLength != -1 {
			failure(w, invalid)
			return
		}
		d := dialog{r: bufio.NewReader(response.Body), w: writer}
		r, e := d.head()
		if e != nil || len(r) != 4 || r[0] != "hello" || r[1] != contract || r[2] != "sc" || r[3] != "http1-duplex-v1" {
			failure(w, invalid)
			return
		}
		daily, e := d.daily()
		if e != nil {
			failure(w, e)
			return
		}
		if d.write("next", 1, daily.nonce) != nil {
			failure(w, invalid)
			return
		}
		all, e := d.all()
		if e != nil {
			failure(w, e)
			return
		}
		if d.write("next", 2, all.nonce) != nil {
			failure(w, invalid)
			return
		}
		data, e := d.state(daily, all)
		if e != nil {
			failure(w, e)
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json")
		w.Write(append(data, '\n'))
	})
}
