// SPDX-License-Identifier: AGPL-3.0-or-later
// Package catalog owns trusted, immutable work packages. It never opens SQLite.
package catalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

const maxPackage = 2 << 20

var identifier = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]*$`)

type Character struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Intro string `json:"intro"`
	Sheet string `json:"sheet"`
}

type Work struct {
	Version    int         `json:"schema_version"`
	ID         string      `json:"work_id"`
	Name       string      `json:"name"`
	Source     string      `json:"source"`
	World      string      `json:"world,omitempty"`
	Characters []Character `json:"characters,omitempty"`
}

type Registry struct {
	works             map[string]Work
	staticCharacters  map[string][]map[string]any
	contentDigest     string
	displayCharacters []map[string]any
}

// Load discovers exactly <root>/<package>/work.json. A broken package fails startup;
// there is no partial registration, hot reload, executable plugin or DB migration.
func Load(roots ...string) (*Registry, error) {
	r := &Registry{works: make(map[string]Work)}
	for _, root := range roots {
		dirs, err := os.ReadDir(root)
		if err != nil {
			return nil, err
		}
		for _, dir := range dirs {
			if !dir.IsDir() {
				continue
			}
			path := filepath.Join(root, dir.Name(), "work.json")
			f, err := os.Open(path)
			if err != nil {
				return nil, fmt.Errorf("package %s: %w", dir.Name(), err)
			}
			data, err := io.ReadAll(io.LimitReader(f, maxPackage+1))
			f.Close()
			if err != nil {
				return nil, err
			}
			if len(data) > maxPackage {
				return nil, fmt.Errorf("package %s too large", dir.Name())
			}
			var w Work
			dec := json.NewDecoder(bytes.NewReader(data))
			dec.DisallowUnknownFields()
			if err := dec.Decode(&w); err != nil {
				return nil, fmt.Errorf("package %s: %w", dir.Name(), err)
			}
			if dec.Decode(new(any)) != io.EOF {
				return nil, fmt.Errorf("package %s: trailing JSON", dir.Name())
			}
			if err := validate(w); err != nil {
				return nil, err
			}
			if _, exists := r.works[w.ID]; exists {
				return nil, fmt.Errorf("duplicate work %s", w.ID)
			}
			r.works[w.ID] = w
		}
	}
	if w, ok := r.works["sc"]; !ok || w.Source != "legacy-sc" {
		return nil, fmt.Errorf("default sc package required")
	}
	return r, nil
}

func validate(w Work) error {
	if w.Version != 1 || !identifier.MatchString(w.ID) || strings.TrimSpace(w.Name) == "" {
		return fmt.Errorf("invalid work identity/version: %q", w.ID)
	}
	if w.Source == "legacy-sc" {
		if w.ID != "sc" || w.World != "" || len(w.Characters) > 0 {
			return fmt.Errorf("legacy-sc must use Python's existing configuration")
		}
		return nil
	}
	if w.Source != "content" || w.ID == "sc" || strings.TrimSpace(w.World) == "" || len(w.Characters) == 0 {
		return fmt.Errorf("invalid content work %s", w.ID)
	}
	seen := make(map[string]bool)
	for _, c := range w.Characters {
		if !identifier.MatchString(c.ID) || seen[c.ID] || strings.TrimSpace(c.Name) == "" || strings.TrimSpace(c.Sheet) == "" {
			return fmt.Errorf("invalid or duplicate character %q in %s", c.ID, w.ID)
		}
		seen[c.ID] = true
	}
	return nil
}

func (r *Registry) descriptions() []map[string]any {
	ids := make([]string, 0, len(r.works))
	for id := range r.works {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		w := r.works[id]
		out = append(out, map[string]any{"work_id": id, "name": w.Name, "default": id == "sc", "capabilities": map[string]any{"catalog": true, "world": w.Source == "content", "conversation": id == "sc", "rag_scope": map[bool]string{true: "legacy-sc-only", false: "unavailable"}[id == "sc"]}})
	}
	return out
}
