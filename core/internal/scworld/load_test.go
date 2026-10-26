// SPDX-License-Identifier: AGPL-3.0-or-later
package scworld

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const minimal = `{"as_of":"2024-02-29","office":{"name":"283","staff":[]},"units":[],"idols":[]}`

func TestWorldSchemaAndNormalization(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world.json")
	write := func(data string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write(minimal)
	b, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]json.RawMessage
	json.Unmarshal(b, &got)
	for _, key := range []string{"releases", "songs", "lives"} {
		if string(got[key]) != "[]" {
			t.Fatalf("%s: missing collection was not normalized", key)
		}
	}
	for name, data := range map[string]string{
		"invalid date": strings.Replace(minimal, "2024-02-29", "2023-02-29", 1),
		"missing":      `{"office":{}}`,
		"wrong type":   strings.Replace(minimal, `"staff":[]`, `"staff":null`, 1),
		"trailing":     minimal + `{}`,
		"utf8":         minimal + string([]byte{0xff}),
		"oversized":    strings.Repeat(" ", MaxBytes+1),
	} {
		write(data)
		if _, err := Load(path); err == nil {
			t.Errorf("%s: accepted invalid source", name)
		}
	}
	write(strings.Replace(minimal, `"idols":[]`, `"idols":[{"name":"真乃","reading":"まの","unit":null,"cv":null,"sources":[]}]`, 1))
	if b, err := Load(path); err != nil || !strings.Contains(string(b), `"cv":null`) {
		t.Fatalf("nullable internal fields lost: %s, %v", b, err)
	}
}
