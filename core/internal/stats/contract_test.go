// SPDX-License-Identifier: AGPL-3.0-or-later
package stats

import (
	"strings"
	"testing"
)

func TestStreamMustBeCompleteBoundedAndWellTyped(t *testing.T) {
	for _, c := range []struct{ name, raw string }{
		{"missing terminator", strings.TrimSuffix(emptyStream, "[\"end\"]\n")},
		{"trailing record", emptyStream + "[\"end\"]\n"},
		{"wrong work", strings.ReplaceAll(emptyStream, `"sc"`, `"demo"`)},
		{"wrong version", strings.ReplaceAll(emptyStream, `"meta",1`, `"meta",2`)},
		{"nullable achievement count", strings.ReplaceAll(emptyStream, `false,null`, `false,{"stages":null,"complete":0}`)},
		{"unknown record", strings.ReplaceAll(emptyStream, `["end"]`, `["secret",10]`)},
		{"oversize record", strings.ReplaceAll(emptyStream, `"mano"`, `"`+strings.Repeat("x", maxRecord)+`"`)},
	} {
		if result, err := Compute(strings.NewReader(c.raw)); err == nil || result != nil {
			t.Fatalf("%s accepted malformed/private data", c.name)
		}
	}
	// An individual row close to the limit remains valid; no total history cap is imposed.
	near := strings.ReplaceAll(emptyStream, `"mano"`, `"`+strings.Repeat("x", maxRecord-100)+`"`)
	if _, err := Compute(strings.NewReader(near)); err != nil {
		t.Fatalf("valid near-limit row: %v", err)
	}
}
