// SPDX-License-Identifier: AGPL-3.0-or-later
// Package content defines private, versioned content snapshots, never HTTP responses.
package content

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type Characters struct {
	WorkID string          `json:"work_id"`
	Format string          `json:"format"`
	Data   json.RawMessage `json:"data"`
}

// Bundle publishes worlds and characters from one export. Payload is a JSON string
// so its digest is independent of JSON re-encoding rules in another language.
type Bundle struct {
	Digest     string       `json:"-"`
	Version    int          `json:"-"`
	Worlds     []World      `json:"worlds"`
	Characters []Characters `json:"characters"`
}
type envelope struct {
	Version int    `json:"schema_version"`
	Payload string `json:"payload"`
	SHA256  string `json:"sha256"`
}

func WriteBundle(path string, bundle Bundle) error {
	if err := validateBundle(bundle); err != nil {
		return err
	}
	b, err := json.Marshal(bundle)
	if err != nil {
		return err
	}
	version := bundle.Version
	if version == 0 {
		version = 2
	}
	if version != 2 && version != 3 {
		return fmt.Errorf("unsupported bundle version")
	}
	data, err := json.Marshal(envelope{version, string(b), fmt.Sprintf("%x", sha256.Sum256(b))})
	if err != nil {
		return err
	}
	return publish(path, data)
}

func ReadBundle(path string) (Bundle, error) {
	var result Bundle
	f, err := os.Open(path)
	if err != nil {
		return result, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, (32<<20)+1))
	if err != nil || len(b) > 32<<20 {
		return result, fmt.Errorf("invalid content snapshot size")
	}
	var e envelope
	if json.Unmarshal(b, &e) != nil || (e.Version != 2 && e.Version != 3) || e.SHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(e.Payload))) {
		return result, fmt.Errorf("invalid content snapshot version/digest")
	}
	if err := json.Unmarshal([]byte(e.Payload), &result); err != nil {
		return result, fmt.Errorf("invalid content payload")
	}
	result.Version = e.Version
	result.Digest = e.SHA256
	return result, validateBundle(result)
}

func validateBundle(b Bundle) error {
	worlds := map[string]bool{}
	for _, w := range b.Worlds {
		if w.WorkID == "" || worlds[w.WorkID] || w.Format == "" || !json.Valid(w.Data) {
			return fmt.Errorf("invalid snapshot world")
		}
		worlds[w.WorkID] = true
	}
	seen := map[string]bool{}
	for _, c := range b.Characters {
		if !worlds[c.WorkID] || seen[c.WorkID] || c.Format == "" || !json.Valid(c.Data) {
			return fmt.Errorf("invalid snapshot character set")
		}
		seen[c.WorkID] = true
	}
	if len(seen) != len(worlds) || !seen["sc"] {
		return fmt.Errorf("incomplete snapshot")
	}
	return nil
}

// World keeps work-specific semantics out of the common registry. Format names the
// adapter contract, not a promise that another work has SC's staff, units or lives.
type World struct {
	WorkID string          `json:"work_id"`
	Format string          `json:"format"`
	Data   json.RawMessage `json:"data"`
}

type Snapshot struct {
	Version int     `json:"schema_version"`
	Worlds  []World `json:"worlds"`
}

// Write publishes a complete snapshot by same-directory rename. An invalid load or
// interrupted write leaves the previous snapshot intact. Private data stays 0600.
func Write(path string, worlds []World) error {
	seen := map[string]bool{}
	for _, w := range worlds {
		if w.WorkID == "" || seen[w.WorkID] || w.Format == "" || !json.Valid(w.Data) {
			return fmt.Errorf("invalid or duplicate snapshot world %q", w.WorkID)
		}
		seen[w.WorkID] = true
	}
	data, err := json.Marshal(Snapshot{Version: 1, Worlds: worlds})
	if err != nil {
		return err
	}
	return publish(path, data)
}

func publish(path string, data []byte) error {
	if len(data) > 32<<20 {
		return fmt.Errorf("content snapshot exceeds 32 MiB")
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".content-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if _, err = f.Write(data); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}
