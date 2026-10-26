// SPDX-License-Identifier: AGPL-3.0-or-later
package sccharacters

import (
	"strings"
	"testing"
)

func TestFullGiftDefinitions(t *testing.T) {
	source := `[[gifts]]
id="cake"
name=" Cake "
category="food"
rarity="common"
[[gifts]]
id="tea"
name="Tea"
category="drink"
rarity="uncommon"
[[gifts]]
id="flower"
name="Flower"
category="flower"
rarity="rare"
`
	gifts, err := parseGifts([]byte(source))
	if err != nil {
		t.Fatal(err)
	}
	if len(gifts) != 3 || gifts[0].Name != " Cake " || gifts[0].Retired {
		t.Fatal("gift order/default/name semantics changed")
	}
	for label, input := range map[string]string{
		"toml": "[[bad", "duplicate": source + source,
		"category":       strings.Replace(source, `category="food"`, `category="unknown"`, 1),
		"rarity":         strings.Replace(source, `rarity="common"`, `rarity="unknown"`, 1),
		"missing":        strings.Replace(source, `name="Tea"`, "", 1),
		"retired type":   source + "retired=1\n",
		"no active rare": source + "retired=true\n",
	} {
		if _, err := parseGifts([]byte(input)); err == nil {
			t.Errorf("%s accepted", label)
		}
	}
}
