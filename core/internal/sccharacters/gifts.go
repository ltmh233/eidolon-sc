// SPDX-License-Identifier: AGPL-3.0-or-later
package sccharacters

import (
	"fmt"
	"github.com/pelletier/go-toml/v2"
	"regexp"
	"strings"
	"unicode/utf8"
)

type Gift struct {
	ID       string `json:"id" toml:"id"`
	Name     string `json:"name" toml:"name"`
	Category string `json:"category" toml:"category"`
	Rarity   string `json:"rarity" toml:"rarity"`
	Retired  bool   `json:"retired" toml:"retired"`
}

func parseGifts(data []byte) ([]Gift, error) {
	var config struct {
		Gifts []Gift `toml:"gifts"`
	}
	if !utf8.Valid(data) || toml.Unmarshal(data, &config) != nil {
		return nil, fmt.Errorf("invalid gifts TOML")
	}
	validID := regexp.MustCompile(`^[a-z0-9_-]+$`)
	categories := map[string]bool{"food": true, "drink": true, "flower": true, "trinket": true}
	rarities := map[string]bool{"common": true, "uncommon": true, "rare": true}
	seen, active := map[string]bool{}, map[string]bool{}
	for _, g := range config.Gifts {
		if !validID.MatchString(g.ID) || seen[g.ID] || strings.TrimSpace(g.Name) == "" || !categories[g.Category] || !rarities[g.Rarity] {
			return nil, fmt.Errorf("invalid gift static definition")
		}
		seen[g.ID] = true
		if !g.Retired {
			active[g.Rarity] = true
		}
	}
	for r := range rarities {
		if !active[r] {
			return nil, fmt.Errorf("gift rarity %s has no active gift", r)
		}
	}
	return config.Gifts, nil
}
