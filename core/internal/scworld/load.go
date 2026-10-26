// SPDX-License-Identifier: AGPL-3.0-or-later
// Package scworld adapts SC's reviewed static world.json. Prompt and retrieval
// rendering remain runtime responsibilities; no CV data is made public here.
package scworld

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"time"
	"unicode/utf8"
)

const MaxBytes = 16 << 20

// rule describes only the SC source schema. Optional collections are normalized
// to empty arrays, like World.from_json; nullable strings remain null. Unknown
// fields are ignored, as in the old loader, rather than silently reaching runtime.
type rule struct {
	kind     string
	fields   map[string]rule
	element  *rule
	optional bool
}

var text = rule{kind: "string"}
var nullable = rule{kind: "nullable-string"}
var day = rule{kind: "date"}

func list(element rule) rule { return rule{kind: "array", element: &element} }
func optionalList(element rule) rule {
	r := list(element)
	r.optional = true
	return r
}
func object(fields map[string]rule) rule { return rule{kind: "object", fields: fields} }

var source = object(map[string]rule{
	"as_of": day,
	"office": object(map[string]rule{
		"name": text,
		"staff": list(object(map[string]rule{
			"name": text, "reading": text, "position": text, "sources": list(text),
		})),
	}),
	"units": list(object(map[string]rule{
		"name": text, "reading": nullable, "members": list(text), "sources": list(text),
	})),
	"idols": list(object(map[string]rule{
		"name": text, "reading": text, "unit": nullable, "cv": nullable, "sources": list(text),
	})),
	"releases": optionalList(object(map[string]rule{
		"catalog": text, "title": text, "date": day, "sources": list(text),
		"tracks": list(object(map[string]rule{"title": text, "song": nullable})),
	})),
	"songs": optionalList(object(map[string]rule{
		"id": text, "title": text, "singers": list(text), "special_unit": nullable,
		"releases": list(text), "sources": list(text),
	})),
	"lives": optionalList(object(map[string]rule{
		"name": text, "sources": list(text),
		"days": list(object(map[string]rule{
			"date": day, "venue": text, "performers": list(text), "absent": list(text), "sources": list(text),
			"setlist": optionalList(object(map[string]rule{
				"title": text, "singers": list(text), "dancers": list(text), "brand": nullable,
			})),
		})),
	})),
})

func (r rule) normalize(value any, path string) (any, error) {
	invalid := func() (any, error) { return nil, fmt.Errorf("%s: expected %s", path, r.kind) }
	switch r.kind {
	case "string", "nullable-string", "date":
		if value == nil && r.kind == "nullable-string" {
			return nil, nil
		}
		s, ok := value.(string)
		if !ok {
			return invalid()
		}
		if r.kind == "date" {
			if parsed, err := time.Parse("2006-01-02", s); err != nil || parsed.Year() < 1 {
				return nil, fmt.Errorf("%s: expected ISO calendar date", path)
			}
		}
		return s, nil
	case "array":
		values, ok := value.([]any)
		if !ok {
			return invalid()
		}
		out := make([]any, 0, len(values))
		for i, v := range values {
			n, err := r.element.normalize(v, fmt.Sprintf("%s[%d]", path, i))
			if err != nil {
				return nil, err
			}
			out = append(out, n)
		}
		return out, nil
	case "object":
		fields, ok := value.(map[string]any)
		if !ok {
			return invalid()
		}
		out := map[string]any{}
		for key, child := range r.fields {
			v, exists := fields[key]
			if !exists {
				if !child.optional {
					return nil, fmt.Errorf("%s.%s: missing field", path, key)
				}
				v = []any{}
			}
			n, err := child.normalize(v, path+"."+key)
			if err != nil {
				return nil, err
			}
			out[key] = n
		}
		return out, nil
	}
	return invalid()
}

func Load(path string) (json.RawMessage, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, MaxBytes+1))
	if err != nil {
		return nil, err
	}
	return Parse(b)
}

// Parse validates bytes already read through a confined content root.
func Parse(b []byte) (json.RawMessage, error) {
	if len(b) > MaxBytes || !utf8.Valid(b) {
		return nil, fmt.Errorf("world exceeds 16 MiB or is not UTF-8")
	}
	decoder := json.NewDecoder(bytes.NewReader(b))
	var raw any
	if err := decoder.Decode(&raw); err != nil {
		return nil, err
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, fmt.Errorf("trailing JSON")
	}
	normalized, err := source.normalize(raw, "sc.world")
	if err != nil {
		return nil, err
	}
	return json.Marshal(normalized)
}
