// SPDX-License-Identifier: AGPL-3.0-or-later
// Package timeline assembles SC history; it never authenticates users or accesses SQLite.
package timeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"sort"
	"time"
)

var invalid = errors.New("invalid timeline contract")

type position struct {
	kind, role   string
	id           int64
	at           time.Time
	aware        bool
	comparison   bool
	offset, size int64
}

// Project validates the complete private stream before writing the public array.
// Memory retains indexes and at most one record; message size is not artificially capped.
func Project(ctx context.Context, input io.Reader, output io.Writer, conversation string) error {
	return projectTimeline(ctx, input, output, conversation, false)
}

func projectPythonTimeline(ctx context.Context, input io.Reader, output io.Writer, conversation string) error {
	return projectTimeline(ctx, input, output, conversation, true)
}

func projectTimeline(ctx context.Context, input io.Reader, output io.Writer, conversation string, rawFields bool) error {
	spool, err := temporary()
	if err != nil {
		return err
	}
	defer spool.Close()
	decoder := json.NewDecoder(input)
	var messages, pending []position
	seen := map[string]map[int64]bool{"message": {}, "event": {}, "reach_out": {}}
	started, ended := false, false
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		var row []json.RawMessage
		err := decoder.Decode(&row)
		if err == io.EOF {
			if ended {
				break
			}
			return invalid
		}
		if err != nil || ended || len(row) == 0 {
			return invalid
		}
		if rawFields {
			for _, field := range row {
				if !validRawUnicode(field) {
					return invalid
				}
			}
		}
		var kind string
		if json.Unmarshal(row[0], &kind) != nil {
			return invalid
		}
		if !started {
			var version int
			var work, id string
			expectedMeta, expectedVersion := 4, 1
			if rawFields {
				expectedMeta, expectedVersion = 5, 2
			}
			if len(row) != expectedMeta || kind != "meta" || json.Unmarshal(row[1], &version) != nil || version != expectedVersion || json.Unmarshal(row[2], &work) != nil || work != "sc" || json.Unmarshal(row[3], &id) != nil || id != conversation {
				return invalid
			}
			if rawFields {
				var contract string
				if json.Unmarshal(row[4], &contract) != nil || contract != displayContract {
					return invalid
				}
			}
			started = true
			continue
		}
		if kind == "end" {
			if len(row) != 1 {
				return invalid
			}
			ended = true
			continue
		}
		expected := 2
		if kind == "event" {
			expected = 3
		}
		if len(row) != expected {
			return invalid
		}
		public, index, err := projectRecordFields(kind, row[1], rawFields)
		if err != nil {
			return err
		}
		// The raw v2 history contract also requires Python comparison keys.
		if rawFields && !index.comparison {
			return invalid
		}
		if kind == "event" {
			var name *string
			if json.Unmarshal(row[2], &name) != nil {
				return invalid
			}
			gift := public["gift_id"]
			if gift != nil {
				if name == nil {
					return invalid
				}
				public["gift_name"] = *name
			} else if name != nil {
				return invalid
			}
		}
		if seen[kind][index.id] {
			return invalid
		}
		seen[kind][index.id] = true
		index.offset, err = spool.Seek(0, io.SeekCurrent)
		if err != nil {
			return err
		}
		if err = json.NewEncoder(spool).Encode(public); err != nil {
			return err
		}
		end, err := spool.Seek(0, io.SeekCurrent)
		if err != nil {
			return err
		}
		index.size = end - index.offset
		if kind == "message" {
			messages = append(messages, index)
		} else {
			pending = append(pending, index)
		}
	}
	sort.Slice(messages, func(i, j int) bool { return messages[i].id < messages[j].id })
	for i := 1; i < len(pending); i++ {
		if pending[i].aware != pending[0].aware {
			return legacyPreviewFailure
		}
	}
	sort.Slice(pending, func(i, j int) bool {
		a, b := pending[i], pending[j]
		if !a.at.Equal(b.at) {
			return a.at.Before(b.at)
		}
		if a.kind != b.kind {
			return a.kind == "event"
		}
		return a.id < b.id
	})
	if _, err = io.WriteString(output, "["); err != nil {
		return err
	}
	written := false
	write := func(p position) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if written {
			if _, err := io.WriteString(output, ","); err != nil {
				return err
			}
		}
		written = true
		_, err := io.CopyN(output, io.NewSectionReader(spool, p.offset, p.size), p.size)
		return err
	}
	cursor := 0
	for _, m := range messages {
		if m.role == "user" {
			for cursor < len(pending) {
				if pending[cursor].aware != m.aware {
					return legacyPreviewFailure
				}
				if !pending[cursor].at.Before(m.at) {
					break
				}
				if err = write(pending[cursor]); err != nil {
					return err
				}
				cursor++
			}
		}
		if err = write(m); err != nil {
			return err
		}
	}
	for ; cursor < len(pending); cursor++ {
		if err = write(pending[cursor]); err != nil {
			return err
		}
	}
	_, err = io.WriteString(output, "]")
	return err
}

func projectRecordFields(kind string, data []byte, rawFields bool) (map[string]any, position, error) {
	var raw map[string]json.RawMessage
	if json.Unmarshal(data, &raw) != nil {
		return nil, position{}, invalid
	}
	fields := map[string][]string{
		"message":   {"id", "role", "text", "ja", "zh", "created_at", "segments", "affinity", "photo"},
		"event":     {"id", "kind", "created_at", "affinity", "gift_id", "liked", "type"},
		"reach_out": {"id", "ja", "zh", "segments", "created_at", "type"},
	}
	keys, ok := fields[kind]
	_, hasComparison := raw["comparison_order"]
	expected := len(keys)
	if hasComparison {
		expected++
	}
	if !ok || len(raw) != expected {
		return nil, position{}, invalid
	}
	for _, key := range keys {
		if _, ok = raw[key]; !ok {
			return nil, position{}, invalid
		}
	}
	if rawFields && kind != "event" {
		var role string
		json.Unmarshal(raw["role"], &role)
		if kind == "reach_out" || role == "character" {
			var segments []map[string]json.RawMessage
			if json.Unmarshal(raw["segments"], &segments) != nil {
				return nil, position{}, invalid
			}
			for _, segment := range segments {
				for _, language := range []string{"ja", "zh"} {
					var value *string
					if json.Unmarshal(segment[language], &value) != nil || value == nil {
						return nil, position{}, invalid
					}
				}
			}
		}
	}
	var record struct {
		Comparison *comparisonOrder `json:"comparison_order"`
		ID         int64            `json:"id"`
		Role       string           `json:"role"`
		Type       string           `json:"type"`
		Kind       string           `json:"kind"`
		Text       *string          `json:"text"`
		Ja         *string          `json:"ja"`
		Zh         *string          `json:"zh"`
		At         string           `json:"created_at"`
		Segments   []struct {
			Ja string `json:"ja"`
			Zh string `json:"zh"`
		} `json:"segments"`
		Affinity json.RawMessage `json:"affinity"`
		Photo    json.RawMessage `json:"photo"`
		Gift     *string         `json:"gift_id"`
		Liked    bool            `json:"liked"`
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if dec.Decode(&record) != nil || bytes.Equal(raw["id"], []byte("null")) {
		return nil, position{}, invalid
	}
	var at time.Time
	aware := true
	if hasComparison {
		if !validOrder(record.Comparison) {
			return nil, position{}, invalid
		}
		at = time.UnixMicro(*record.Comparison.Micros)
		aware = *record.Comparison.Aware
	} else {
		var err error
		at, err = time.Parse(time.RFC3339Nano, record.At)
		if err != nil {
			at, err = time.Parse("2006-01-02T15:04:05.999999999-07:00:00", record.At)
		}
		if err != nil {
			return nil, position{}, invalid
		}
		at = at.Truncate(time.Microsecond)
	}
	public := map[string]any{"id": record.ID, "created_at": record.At}
	if kind == "event" {
		if record.Type != "event" || record.Kind == "" {
			return nil, position{}, invalid
		}
		public["type"] = "event"
		public["kind"] = record.Kind
		public["affinity"] = record.Affinity
		if record.Gift != nil {
			public["gift_id"] = *record.Gift
		}
	} else {
		if record.Segments == nil {
			return nil, position{}, invalid
		}
		if rawFields && (kind == "reach_out" || record.Role == "character") {
			if record.Ja != nil {
				value := visibleText(*record.Ja)
				record.Ja = &value
			}
			if record.Zh != nil {
				value := visibleText(*record.Zh)
				record.Zh = &value
			}
			for i := range record.Segments {
				record.Segments[i].Ja = visibleText(record.Segments[i].Ja)
				record.Segments[i].Zh = visibleText(record.Segments[i].Zh)
			}
		}
		public["ja"] = record.Ja
		public["zh"] = record.Zh
		public["segments"] = record.Segments
		if kind == "reach_out" {
			if record.Type != "reach_out" || record.Ja == nil || record.Zh == nil {
				return nil, position{}, invalid
			}
			public["type"] = "reach_out"
		} else {
			if record.Role != "user" && record.Role != "character" {
				return nil, position{}, invalid
			}
			public["role"] = record.Role
			public["text"] = record.Text
			public["affinity"] = record.Affinity
			if !bytes.Equal(bytes.TrimSpace(record.Photo), []byte("null")) {
				var photo map[string]json.RawMessage
				if json.Unmarshal(record.Photo, &photo) != nil || len(photo) != 4 {
					return nil, position{}, invalid
				}
				for _, key := range []string{"id", "status", "reason", "tag"} {
					if _, ok := photo[key]; !ok {
						return nil, position{}, invalid
					}
				}
				var shown struct {
					ID     string  `json:"id"`
					Status string  `json:"status"`
					Reason *string `json:"reason"`
					Tag    string  `json:"tag"`
				}
				if json.Unmarshal(record.Photo, &shown) != nil || shown.ID == "" || shown.Status == "" {
					return nil, position{}, invalid
				}
				public["photo"] = map[string]any{"id": shown.ID, "status": shown.Status, "reason": shown.Reason}
			}
		}
	}
	if kind != "reach_out" && !bytes.Equal(bytes.TrimSpace(record.Affinity), []byte("null")) {
		var fields map[string]json.RawMessage
		if json.Unmarshal(record.Affinity, &fields) != nil || len(fields) != 5 {
			return nil, position{}, invalid
		}
		for _, field := range fields {
			if bytes.Equal(bytes.TrimSpace(field), []byte("null")) {
				return nil, position{}, invalid
			}
		}
		var change struct {
			Up          bool   `json:"up"`
			TierChanged bool   `json:"tier_changed"`
			Tier        string `json:"tier"`
			Cooldown    bool   `json:"cooldown"`
			Thawed      bool   `json:"thawed"`
		}
		check := json.NewDecoder(bytes.NewReader(record.Affinity))
		check.DisallowUnknownFields()
		if check.Decode(&change) != nil {
			return nil, position{}, invalid
		}
	}
	return public, position{kind: kind, role: record.Role, id: record.ID, at: at, aware: aware, comparison: hasComparison}, nil
}
