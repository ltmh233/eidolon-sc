// SPDX-License-Identifier: AGPL-3.0-or-later
// Package sccharacters reads static SC inputs. Facts segmentation and voice cue
// interpretation remain Python runtime semantics, not a second Go implementation.
package sccharacters

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"eidolon/core/internal/scworld"
	"github.com/pelletier/go-toml/v2"
)

type Data struct {
	Gifts     []Gift              `json:"gifts,omitempty"`
	Config    string              `json:"config"`
	Entries   []map[string]any    `json:"entries"`
	Documents map[string]*string  `json:"documents"`
	Readings  []map[string]string `json:"readings"`
	Manifest  map[string]*string  `json:"manifest"`
}

var id = regexp.MustCompile(`^[a-z][a-z0-9_-]*$`)
var color = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
var outfit = regexp.MustCompile(`^(stand|stand_costume)/[0-9]{10}$`)

func World(path string) (json.RawMessage, error) {
	root, err := os.OpenRoot(filepath.Dir(path))
	if err != nil {
		return nil, err
	}
	defer root.Close()
	b, err := read(root, "world.json", false)
	if err != nil {
		return nil, err
	}
	return scworld.Parse(b)
}

// read is confined even for missing optional files: os.Root prevents symlink
// escape on open. Dotfiles, traversal and absolute references are never sources.
func read(root *os.Root, name string, optional bool) ([]byte, error) {
	if !filepath.IsLocal(name) || strings.Contains(name, "\\") {
		return nil, fmt.Errorf("invalid content path")
	}
	for _, part := range strings.Split(filepath.ToSlash(name), "/") {
		if part == "" || strings.HasPrefix(part, ".") {
			return nil, fmt.Errorf("invalid content path")
		}
	}
	f, err := root.Open(name)
	if optional && os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("content file %s unavailable", name)
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() {
		return nil, fmt.Errorf("content file %s is not regular", name)
	}
	b, err := io.ReadAll(io.LimitReader(f, (16<<20)+1))
	if err != nil || len(b) > 16<<20 {
		return nil, fmt.Errorf("content file %s exceeds limit or is unreadable", name)
	}
	return b, nil
}

func Load(path string, world json.RawMessage) (Data, error) {
	return load(path, world, false)
}

func LoadRelease(path string, world json.RawMessage) (Data, error) { return load(path, world, true) }

func load(path string, world json.RawMessage, withGifts bool) (Data, error) {
	result := Data{Config: filepath.Base(path), Documents: map[string]*string{}, Manifest: map[string]*string{}, Readings: []map[string]string{}}
	root, err := os.OpenRoot(filepath.Dir(path))
	if err != nil {
		return result, err
	}
	defer root.Close()
	total := 0
	take := func(name string, optional bool) ([]byte, error) {
		b, err := read(root, name, optional)
		if err != nil {
			return nil, err
		}
		total += len(b)
		if total > 64<<20 {
			return nil, fmt.Errorf("SC content inputs exceed 64 MiB")
		}
		var sum *string
		if b != nil {
			s := fmt.Sprintf("%x", sha256.Sum256(b))
			sum = &s
		}
		result.Manifest[name] = sum
		return b, nil
	}
	b, err := take(result.Config, false)
	if err != nil {
		return result, err
	}
	var config struct {
		Characters []map[string]any `toml:"characters"`
	}
	if !utf8.Valid(b) || toml.Unmarshal(b, &config) != nil {
		return result, fmt.Errorf("invalid characters TOML")
	}
	if len(config.Characters) == 0 {
		return result, fmt.Errorf("no SC characters")
	}
	// Hash the exact raw world from the same root. Compare normalized semantics in
	// the exporter; consumers verify this manifest before using remaining resources.
	worldBytes, err := take("world.json", false)
	if err != nil {
		return result, err
	}
	normalized, err := scworld.Parse(worldBytes)
	if err != nil || string(normalized) != string(world) {
		return result, fmt.Errorf("world changed during export")
	}
	gifts, err := take("gifts.toml", false)
	if err != nil {
		return result, err
	}
	if withGifts {
		result.Gifts, err = parseGifts(gifts)
		if err != nil {
			return result, err
		}
	}
	var giftConfig struct {
		Gifts []struct {
			ID     string `toml:"id"`
			Rarity string `toml:"rarity"`
		} `toml:"gifts"`
	}
	if !utf8.Valid(gifts) || toml.Unmarshal(gifts, &giftConfig) != nil {
		return result, fmt.Errorf("invalid gifts TOML")
	}
	giftIDs := map[string]string{}
	for _, g := range giftConfig.Gifts {
		if _, ok := giftIDs[g.ID]; ok {
			return result, fmt.Errorf("duplicate gift")
		}
		giftIDs[g.ID] = g.Rarity
	}
	readings, err := take("readings.toml", true)
	if err != nil {
		return result, err
	}
	if readings != nil {
		var r struct {
			Readings []map[string]any `toml:"readings"`
		}
		if !utf8.Valid(readings) || toml.Unmarshal(readings, &r) != nil {
			return result, fmt.Errorf("invalid readings TOML")
		}
		seen := map[string]bool{}
		for _, e := range r.Readings {
			v := map[string]string{}
			for _, k := range []string{"written", "kana", "phonemes"} {
				s, ok := e[k].(string)
				if !ok {
					return result, fmt.Errorf("invalid reading %s", k)
				}
				v[k] = strings.TrimSpace(s)
			}
			if v["written"] == "" || seen[v["written"]] || strings.Contains(v["phonemes"], "<|phoneme_start|>") || strings.Contains(v["phonemes"], "<|phoneme_end|>") {
				return result, fmt.Errorf("invalid/duplicate reading")
			}
			seen[v["written"]] = true
			result.Readings = append(result.Readings, v)
		}
	}
	var w struct {
		Idols  []struct{ Name string } `json:"idols"`
		Office struct {
			Staff []struct{ Name string } `json:"staff"`
		} `json:"office"`
	}
	if json.Unmarshal(world, &w) != nil {
		return result, fmt.Errorf("invalid SC world")
	}
	names := map[string]bool{}
	for _, v := range w.Idols {
		names[v.Name] = true
	}
	for _, v := range w.Office.Staff {
		names[v.Name] = true
	}
	ids, speakers := map[string]bool{}, map[string]bool{}
	for _, raw := range config.Characters {
		e, err := normalize(raw)
		if err != nil {
			return result, err
		}
		label := e["id"].(string)
		if ids[label] || !names[e["name"].(string)] {
			return result, fmt.Errorf("character %s duplicate or absent from SC world", label)
		}
		ids[label] = true
		for _, s := range e["speakers"].([]string) {
			if speakers[s] {
				return result, fmt.Errorf("duplicate character speaker")
			}
		}
		for _, s := range e["speakers"].([]string) {
			speakers[s] = true
		}
		liked := e["liked_gifts"].([]string)
		seen := map[string]bool{}
		nonRare := false
		if len(liked) < 1 || len(liked) > 3 {
			return result, fmt.Errorf("character %s invalid liked_gifts", label)
		}
		for _, g := range liked {
			rarity, ok := giftIDs[g]
			if !ok || seen[g] {
				return result, fmt.Errorf("character %s invalid gift reference", label)
			}
			seen[g] = true
			nonRare = nonRare || rarity != "rare"
		}
		if !nonRare {
			return result, fmt.Errorf("character %s likes only rare gifts", label)
		}
		for _, k := range []string{"sheet", "facts", "avatar", "background", "scsp_avatar"} {
			name, ok := e[k].(string)
			if !ok {
				continue
			}
			b, err := take(name, k != "sheet")
			if err != nil {
				return result, fmt.Errorf("character %s %s: %w", label, k, err)
			}
			if k == "sheet" || k == "facts" {
				var text *string
				if b != nil {
					if !utf8.Valid(b) {
						return result, fmt.Errorf("character %s invalid text encoding", label)
					}
					s := string(b)
					text = &s
				}
				result.Documents[name] = text
			}
		}
		result.Entries = append(result.Entries, e)
	}
	// Detect inputs changing during export; never publish a mixed source batch.
	for name, sum := range result.Manifest {
		b, err := read(root, name, sum == nil)
		if err != nil {
			return result, err
		}
		if (sum == nil) != (b == nil) || sum != nil && *sum != fmt.Sprintf("%x", sha256.Sum256(b)) {
			return result, fmt.Errorf("content changed during export")
		}
	}
	return result, nil
}

func normalize(raw map[string]any) (map[string]any, error) {
	e := map[string]any{}
	for _, k := range []string{"id", "name", "intro", "voice_id", "sheet", "color", "birthday"} {
		v, ok := raw[k].(string)
		if !ok {
			return nil, fmt.Errorf("character missing %s", k)
		}
		e[k] = v
	}
	label := e["id"].(string)
	bad := func(k string) (map[string]any, error) { return nil, fmt.Errorf("character %s invalid %s", label, k) }
	if !id.MatchString(label) {
		return bad("id")
	}
	if !color.MatchString(e["color"].(string)) {
		return bad("color")
	}
	e["color"] = strings.ToLower(e["color"].(string))
	if _, err := time.Parse("2006-01-02", "2000-"+e["birthday"].(string)); err != nil || len(e["birthday"].(string)) != 5 {
		return bad("birthday")
	}
	for _, k := range []string{"speakers", "liked_gifts"} {
		values := []string{}
		if v, exists := raw[k]; exists {
			list, ok := v.([]any)
			if !ok {
				return bad(k)
			}
			for _, item := range list {
				s, ok := item.(string)
				if !ok || k == "speakers" && s == "" {
					return bad(k)
				}
				values = append(values, s)
			}
		}
		e[k] = values
	}
	for _, k := range []string{"facts", "avatar", "background", "scsp_avatar"} {
		if s, ok := raw[k].(string); ok {
			e[k] = s
		}
	}
	for _, k := range []string{"name_zh", "name_kana", "name_romaji", "photo_tag", "photo_appearance"} {
		if v, exists := raw[k]; exists {
			s, ok := v.(string)
			if !ok || strings.TrimSpace(s) == "" || !singleLine(s) {
				return bad(k)
			}
			e[k] = strings.TrimSpace(s)
		}
	}
	if v, ok := raw["idol_id"]; ok {
		i, valid := v.(int64)
		if !valid || i <= 0 {
			return bad("idol_id")
		}
		e["idol_id"] = i
	}
	if v, ok := raw["default_outfit"]; ok {
		s, valid := v.(string)
		if !valid || !outfit.MatchString(s) {
			return bad("default_outfit")
		}
		e["default_outfit"] = s
	}
	for _, k := range []string{"greeting", "call_opener"} {
		_, a := raw[k]
		_, b := raw[k+"_subtitle"]
		if !a && !b {
			continue
		}
		for _, field := range []string{k, k + "_subtitle"} {
			s, ok := raw[field].(string)
			if !ok || strings.TrimSpace(s) == "" {
				return bad(field)
			}
			e[field] = strings.TrimSpace(s)
		}
	}
	return e, nil
}

func singleLine(s string) bool {
	const endings = "\n\r\v\f\x1c\x1d\x1e\u0085\u2028\u2029"
	if strings.HasSuffix(s, "\r\n") {
		s = strings.TrimSuffix(s, "\r\n")
	} else {
		runes := []rune(s)
		if len(runes) > 0 && strings.ContainsRune(endings, runes[len(runes)-1]) {
			s = string(runes[:len(runes)-1])
		}
	}
	return !strings.ContainsAny(s, endings)
}
