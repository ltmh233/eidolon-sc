// SPDX-License-Identifier: AGPL-3.0-or-later
package catalog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"eidolon/core/internal/content"
)

func TestPrivateSnapshotPublication(t *testing.T) {
	dir := t.TempDir()
	source, dest := filepath.Join(dir, "world.json"), filepath.Join(dir, "snapshot.json")
	data := `{"as_of":"2026-10-25","office":{"name":"283","staff":[]},"units":[],"idols":[]}`
	if err := os.WriteFile(source, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	r := registry(t)
	if err := ExportWorlds(r, source, dest); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(dest)
	if err != nil {
		t.Fatal(err)
	}
	var s content.Snapshot
	if err := json.Unmarshal(before, &s); err != nil {
		t.Fatal(err)
	}
	if s.Version != 1 || len(s.Worlds) != 2 || s.Worlds[0].WorkID != "demo" || s.Worlds[1].WorkID != "sc" || s.Worlds[1].Format != "sc-world-v1" || s.Worlds[0].Format != "text-world-v1" {
		t.Fatalf("world identity/adapter lost: %+v", s)
	}
	info, _ := os.Stat(dest)
	if info.Mode().Perm() != 0600 {
		t.Fatalf("private snapshot permissions: %v", info.Mode())
	}
	if err := os.WriteFile(source, []byte(`{"as_of":"broken"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := ExportWorlds(r, source, dest); err == nil {
		t.Fatal("broken SC source published")
	}
	after, _ := os.ReadFile(dest)
	if string(before) != string(after) {
		t.Fatal("failed load replaced the previous snapshot")
	}
	if err := content.Write(dest, []content.World{s.Worlds[0], s.Worlds[0]}); err == nil {
		t.Fatal("duplicate work published")
	}
	after, _ = os.ReadFile(dest)
	if string(before) != string(after) {
		t.Fatal("invalid snapshot replaced published content")
	}
	if err := content.Write(filepath.Join(dir, "missing", "snapshot.json"), s.Worlds); err == nil {
		t.Fatal("missing output directory accepted")
	}
}

func TestSnapshotRejectsChangedRegisteredPackage(t *testing.T) {
	r := registry(t)
	demo, err := json.Marshal(r.works["demo"].Characters)
	if err != nil {
		t.Fatal(err)
	}
	b := content.Bundle{Worlds: worlds(r, json.RawMessage(`{}`)), Characters: []content.Characters{
		{WorkID: "sc", Format: "sc-characters-v1", Data: json.RawMessage(`{"entries":[{"id":"mano","name":"Mano"}]}`)},
		{WorkID: "demo", Format: "text-characters-v1", Data: demo},
	}}
	path := filepath.Join(t.TempDir(), "bundle.json")
	if err := content.WriteBundle(path, b); err != nil {
		t.Fatal(err)
	}
	if err := AttachContent(r, path); err != nil {
		t.Fatal(err)
	}
	work := r.works["demo"]
	work.World = "changed release"
	r.works["demo"] = work
	if err := AttachContent(r, path); err == nil {
		t.Fatal("changed world silently mixed with old snapshot")
	}
	// Restore the original registry for the separate character change.
	fresh := registry(t)
	r.works["demo"] = fresh.works["demo"]
	work = r.works["demo"]
	work.Characters[0].Intro = "changed release"
	r.works["demo"] = work
	if err := AttachContent(r, path); err == nil {
		t.Fatal("changed character package silently mixed with old snapshot")
	}
	if len(r.staticCharacters["sc"]) != 1 {
		t.Fatal("failed attachment discarded previous state")
	}
}
