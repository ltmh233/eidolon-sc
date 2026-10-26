// SPDX-License-Identifier: AGPL-3.0-or-later
package content

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestBundleIntegrityAndAtomicFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "private.json")
	bundle := Bundle{
		Worlds:     []World{{WorkID: "sc", Format: "sc-world-v1", Data: json.RawMessage(`{}`)}},
		Characters: []Characters{{WorkID: "sc", Format: "sc-characters-v1", Data: json.RawMessage(`{"entries":[]}`)}},
	}
	if err := WriteBundle(path, bundle); err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0600 {
		t.Fatal("snapshot must stay private")
	}
	loaded, err := ReadBundle(path)
	if err != nil || len(loaded.Characters) != 1 {
		t.Fatalf("round trip: %v", err)
	}
	invalid := bundle
	invalid.Characters = append([]Characters{}, bundle.Characters...)
	invalid.Characters = append(invalid.Characters, bundle.Characters[0])
	if err := WriteBundle(path, invalid); err == nil {
		t.Fatal("duplicate scope published")
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("failed validation changed published bytes")
	}
	for label, data := range map[string][]byte{
		"truncated": []byte(`{"schema_version":2`),
		"version":   []byte(`{"schema_version":1}`),
		"digest":    []byte(`{"schema_version":2,"payload":"{}","sha256":"bad"}`),
	} {
		bad := filepath.Join(filepath.Dir(path), label+".json")
		if err := os.WriteFile(bad, data, 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadBundle(bad); err == nil {
			t.Errorf("%s accepted", label)
		}
	}
	if err := WriteBundle(filepath.Join(path, "unwritable.json"), bundle); err == nil {
		t.Fatal("write through regular file succeeded")
	}
	after, err = os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("failed write damaged previous snapshot")
	}
}
