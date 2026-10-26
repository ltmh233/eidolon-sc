// SPDX-License-Identifier: AGPL-3.0-or-later
package catalog

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

// Deliberate public allowlist, including nested fields. Never decode display
// metadata straight into an arbitrary map returned to anonymous clients.
type displayLine struct {
	JA       string  `json:"ja"`
	Subtitle string  `json:"subtitle"`
	AudioURL *string `json:"audio_url"`
}
type displayOutfit struct {
	ID  string `json:"id"`
	URL string `json:"url"`
}
type displayStand struct {
	DefaultOutfit displayOutfit `json:"default_outfit"`
	OutfitsURL    string        `json:"outfits_url"`
}
type displayCharacter struct {
	ID            string        `json:"id"`
	Name          string        `json:"name"`
	Intro         string        `json:"intro"`
	AvatarURL     *string       `json:"avatar_url"`
	SCSPAvatarURL *string       `json:"scsp_avatar_url"`
	Color         string        `json:"color"`
	Birthday      string        `json:"birthday"`
	BackgroundURL *string       `json:"background_url"`
	Stand         *displayStand `json:"stand"`
	IdolID        *int          `json:"idol_id"`
	NameZH        *string       `json:"name_zh"`
	NameKana      *string       `json:"name_kana"`
	NameRomaji    *string       `json:"name_romaji"`
	Unit          *string       `json:"unit"`
	UnitReading   *string       `json:"unit_reading"`
	UnitOrder     *int          `json:"unit_order"`
	Greeting      *displayLine  `json:"greeting"`
	CallOpener    *displayLine  `json:"call_opener"`
	CallPosterURL *string       `json:"call_poster_url"`
	VoxelsURL     *string       `json:"voxels_url"`
}

func AttachDisplay(r *Registry, path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	var publication struct {
		Version    int                `json:"schema_version"`
		Digest     string             `json:"content_sha256"`
		AssetsURL  *string            `json:"assets_url"`
		Characters []displayCharacter `json:"characters"`
	}
	decoder := json.NewDecoder(io.LimitReader(f, (2<<20)+1))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&publication) != nil || decoder.Decode(new(any)) != io.EOF {
		return fmt.Errorf("invalid public catalog metadata")
	}
	if publication.Version != 1 || r.contentDigest == "" || publication.Digest != r.contentDigest {
		return fmt.Errorf("catalog/content release mismatch")
	}
	expected := map[string]bool{}
	for _, c := range r.staticCharacters["sc"] {
		expected[c["local_id"].(string)] = true
	}
	if len(publication.Characters) != len(expected) {
		return fmt.Errorf("catalog character set mismatch")
	}
	listed := []map[string]any{}
	for _, c := range publication.Characters {
		if !expected[c.ID] {
			return fmt.Errorf("catalog character identity mismatch")
		}
		delete(expected, c.ID)
		b, err := json.Marshal(c)
		if err != nil {
			return err
		}
		var display map[string]any
		if err := json.Unmarshal(b, &display); err != nil {
			return err
		}
		listed = append(listed, entry("sc", c.ID, display))
	}
	r.displayCharacters = listed
	return nil
}
