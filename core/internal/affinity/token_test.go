// SPDX-License-Identifier: AGPL-3.0-or-later
package affinity

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCredentialFileIsPurposeLimitedAndOwnerOnly(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	token := strings.Repeat("1", 64)
	if err := os.WriteFile(path, []byte(token+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	got, err := ReadTokenFile(path)
	if err != nil || got != token {
		t.Fatal("valid private credential refused", err)
	}
	for _, c := range []struct {
		data string
		mode os.FileMode
	}{{token, 0644}, {"short", 0600}, {strings.Repeat("A", 64), 0600}} {
		if err := os.WriteFile(path, []byte(c.data), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(path, c.mode); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadTokenFile(path); err == nil {
			t.Fatalf("unsafe credential accepted: mode=%v", c.mode)
		}
	}
}
