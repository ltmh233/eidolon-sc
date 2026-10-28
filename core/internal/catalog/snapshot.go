// SPDX-License-Identifier: AGPL-3.0-or-later
package catalog

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"reflect"
	"sort"

	"eidolon/core/internal/content"
	"eidolon/core/internal/sccharacters"
	"eidolon/core/internal/scworld"
)

// ExportWorlds is an offline, private publication path. No HTTP handler exposes
// this snapshot. All works are loaded before any output file is replaced.
func ExportWorlds(r *Registry, scPath, output string) error {
	sc, err := scworld.Load(scPath)
	if err != nil {
		return err
	}
	return content.Write(output, worlds(r, sc))
}

func worlds(r *Registry, sc json.RawMessage) []content.World {
	ids := make([]string, 0, len(r.works))
	for id := range r.works {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	worlds := make([]content.World, 0, len(ids))
	for _, id := range ids {
		w := r.works[id]
		data, _ := json.Marshal(map[string]string{"setting": w.World})
		format := "text-world-v1"
		if id == "sc" {
			data, format = sc, "sc-world-v1"
		}
		worlds = append(worlds, content.World{WorkID: id, Format: format, Data: data})
	}
	return worlds
}

// ExportCharacters is one atomic batch; the v1 world-only CLI remains available.
func ExportCharacters(r *Registry, charactersPath, worldPath, output string) error {
	return ExportRelease(r, charactersPath, worldPath, output, false)
}

func ExportRelease(r *Registry, charactersPath, worldPath, output string, withGifts bool) error {
	a, _ := filepath.Abs(worldPath)
	b, _ := filepath.Abs(filepath.Join(filepath.Dir(charactersPath), "world.json"))
	if a != b {
		return fmt.Errorf("character bundle requires world.json beside characters TOML")
	}
	world, err := sccharacters.World(charactersPath)
	if err != nil {
		return err
	}
	var sc sccharacters.Data
	if withGifts {
		sc, err = sccharacters.LoadRelease(charactersPath, world)
	} else {
		sc, err = sccharacters.Load(charactersPath, world)
	}
	if err != nil {
		return err
	}
	bundle := content.Bundle{Worlds: worlds(r, world)}
	if withGifts {
		bundle.Version = 3
	}
	for _, w := range bundle.Worlds {
		var data []byte
		format := "text-characters-v1"
		if w.WorkID == "sc" {
			data, err = json.Marshal(sc)
			format = "sc-characters-v1"
			if withGifts {
				format = "sc-characters-v2"
			}
		} else {
			data, err = json.Marshal(r.works[w.WorkID].Characters)
		}
		if err != nil {
			return err
		}
		bundle.Characters = append(bundle.Characters, content.Characters{WorkID: w.WorkID, Format: format, Data: data})
	}
	return content.WriteBundle(output, bundle)
}

// AttachContent installs only an explicit public projection. Full SC display URLs
// remain the legacy endpoint's responsibility; private snapshots are never served.
func AttachContent(r *Registry, path string) error {
	bundle, err := content.ReadBundle(path)
	if err != nil {
		return err
	}
	listed := map[string][]map[string]any{}
	for _, world := range bundle.Worlds {
		work, ok := r.works[world.WorkID]
		if !ok {
			return fmt.Errorf("snapshot world not registered")
		}
		if world.WorkID != "sc" {
			var text struct {
				Setting string `json:"setting"`
			}
			if world.Format != "text-world-v1" || json.Unmarshal(world.Data, &text) != nil || text.Setting != work.World {
				return fmt.Errorf("world package/snapshot mismatch")
			}
		}
	}
	for _, set := range bundle.Characters {
		if _, ok := r.works[set.WorkID]; !ok {
			return fmt.Errorf("snapshot work not registered")
		}
		if _, ok := listed[set.WorkID]; ok {
			return fmt.Errorf("duplicate snapshot work")
		}
		var entries []map[string]any
		if set.WorkID == "sc" {
			if (bundle.Version == 2 && set.Format != "sc-characters-v1") || (bundle.Version == 3 && set.Format != "sc-characters-v2") {
				return fmt.Errorf("invalid SC characters format")
			}
			var data sccharacters.Data
			if json.Unmarshal(set.Data, &data) != nil {
				return fmt.Errorf("invalid SC character data")
			}
			entries = data.Entries
		} else {
			if set.Format != "text-characters-v1" || json.Unmarshal(set.Data, &entries) != nil {
				return fmt.Errorf("invalid character data")
			}
			var source []Character
			if json.Unmarshal(set.Data, &source) != nil || !reflect.DeepEqual(source, r.works[set.WorkID].Characters) {
				return fmt.Errorf("character package/snapshot mismatch")
			}
		}
		if len(entries) == 0 {
			return fmt.Errorf("empty character set")
		}
		seen := map[string]bool{}
		public := []map[string]any{}
		for _, c := range entries {
			id, ok := c["id"].(string)
			if !ok || !identifier.MatchString(id) || seen[id] {
				return fmt.Errorf("invalid character identity")
			}
			seen[id] = true
			display := map[string]any{"id": id}
			for _, key := range []string{"name", "intro", "color", "birthday", "name_zh", "name_kana", "name_romaji"} {
				if value, ok := c[key].(string); ok {
					display[key] = value
				}
			}
			if _, ok := display["name"]; !ok {
				return fmt.Errorf("missing character name")
			}
			public = append(public, entry(set.WorkID, id, display))
		}
		listed[set.WorkID] = public
	}
	if len(listed) != len(r.works) {
		return fmt.Errorf("snapshot work set mismatch")
	}
	r.staticCharacters = listed
	r.contentDigest = bundle.Digest
	r.contentVersion = bundle.Version
	return nil
}

// SCCreateContent exposes only identities from the verified immutable v3 release.
func SCCreateContent(r *Registry) (string, []string, error) {
	if r.contentVersion != 3 || r.contentDigest == "" || len(r.staticCharacters["sc"]) == 0 {
		return "", nil, fmt.Errorf("SC creation requires v3 content-snapshot")
	}
	ids := make([]string, 0, len(r.staticCharacters["sc"]))
	for _, c := range r.staticCharacters["sc"] {
		ids = append(ids, c["local_id"].(string))
	}
	return r.contentDigest, ids, nil
}
