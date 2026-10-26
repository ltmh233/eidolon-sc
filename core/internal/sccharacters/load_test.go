// SPDX-License-Identifier: AGPL-3.0-or-later
package sccharacters

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sample = `[[characters]]
id="mano"
name="Mano"
intro="intro"
voice_id="voice"
sheet="sheet.md"
color="#ABCDEF"
birthday="02-29"
liked_gifts=["cake"]
speakers=["M", "M"]
facts="missing.md"
avatar="missing.png"
name_zh=" Mano "
`

func TestStaticSchemaResourcesAndConfinement(t *testing.T) {
	dir := t.TempDir()
	put := func(name, s string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(s), 0600); err != nil {
			t.Fatal(err)
		}
	}
	put("characters.toml", sample)
	put("sheet.md", "private sheet")
	put("gifts.toml", "[[gifts]]\nid='cake'\nrarity='common'")
	put("world.json", `{"as_of":"2026-10-25","office":{"name":"283","staff":[]},"units":[],"idols":[{"name":"Mano","reading":"mano","unit":null,"cv":null,"sources":[]}]}`)
	path := filepath.Join(dir, "characters.toml")
	world, err := World(path)
	if err != nil {
		t.Fatal(err)
	}
	data, err := Load(path, world)
	if err != nil {
		t.Fatal(err)
	}
	if data.Entries[0]["color"] != "#abcdef" || data.Entries[0]["name_zh"] != "Mano" || data.Documents["missing.md"] != nil || data.Manifest["missing.png"] != nil {
		t.Fatal("normalization/missing optional resource semantics")
	}
	for label, source := range map[string]string{
		"toml": "[[broken", "duplicate": sample + sample,
		"missing required": strings.Replace(sample, "voice_id=\"voice\"", "", 1),
		"birthday":         strings.Replace(sample, "02-29", "02-30", 1),
		"world reference":  strings.Replace(sample, `name="Mano"`, `name="Other"`, 1),
		"gift reference":   strings.Replace(sample, `["cake"]`, `["unknown"]`, 1),
		"missing sheet":    strings.Replace(sample, `sheet="sheet.md"`, `sheet="absent.md"`, 1),
		"parent":           strings.Replace(sample, "sheet.md", "../secret.txt", 1),
		"absolute":         strings.Replace(sample, "sheet.md", "/secret.txt", 1),
		"dotfile":          strings.Replace(sample, "sheet.md", ".env", 1),
		"alias multiline":  strings.Replace(sample, `name_zh=" Mano "`, `name_zh="Mano\n\n"`, 1),
		"bool idol":        sample + "idol_id=true\n",
		"outfit":           sample + "default_outfit='other/123'\n",
		"script pair":      sample + "greeting='hello'\n",
	} {
		put("characters.toml", source)
		if _, err := Load(path, world); err == nil {
			t.Errorf("%s: invalid content accepted", label)
		}
	}
	put("characters.toml", sample)
	outside := filepath.Join(t.TempDir(), "private.txt")
	if err := os.WriteFile(outside, []byte("sentinel"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(dir, "escape.md")); err != nil {
		t.Fatal(err)
	}
	put("characters.toml", strings.Replace(sample, "sheet.md", "escape.md", 1))
	if _, err := Load(path, world); err == nil {
		t.Fatal("symlink escape accepted")
	}
	put("characters.toml", sample)
	put("readings.toml", "[[readings]]\nwritten='M'\nkana='m'\nphonemes='<|phoneme_start|>'")
	if _, err := Load(path, world); err == nil {
		t.Fatal("invalid reading accepted")
	}
}
