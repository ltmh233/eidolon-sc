// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"io"
)

var unknownGift = errors.New("unknown gift in selected preview")
var legacyPreviewFailure = errors.New("selected preview cannot be displayed")

type summaryConversation struct {
	ID        string  `json:"id"`
	Character string  `json:"character_id"`
	CreatedAt string  `json:"created_at"`
	Title     *string `json:"title"`
}
type previewMessage struct {
	DisplayError bool    `json:"display_error"`
	ID           int64   `json:"id"`
	Role         string  `json:"role"`
	Text         *string `json:"text"`
	Ja           *string `json:"ja"`
	Zh           *string `json:"zh"`
	At           string  `json:"created_at"`
	PhotoStatus  *string `json:"photo_status"`
}
type previewEvent struct {
	Order *comparisonOrder `json:"order_key"`
	ID    int64            `json:"id"`
	Kind  string           `json:"kind"`
	At    string           `json:"created_at"`
	Gift  *string          `json:"gift_id"`
	Name  *string          `json:"gift_name"`
}
type previewReachOut struct {
	Order        *comparisonOrder `json:"order_key"`
	DisplayError bool             `json:"display_error"`
	ID           int64            `json:"id"`
	Ja           string           `json:"ja"`
	Zh           string           `json:"zh"`
	At           string           `json:"created_at"`
}
type previewCandidates struct {
	OrderingError bool             `json:"ordering_error"`
	Message       *previewMessage  `json:"message"`
	Event         *previewEvent    `json:"event"`
	ReachOut      *previewReachOut `json:"reach_out"`
	Said          *comparisonOrder `json:"last_turn_key"`
}
type comparisonOrder struct {
	Micros *int64 `json:"micros"`
	Aware  *bool  `json:"aware"`
}

// ProjectSummaries preserves Python's indexed conversation order; only the last-record
// choice and public preview/summary projection move here. The caller buffers privately.
func ProjectSummaries(ctx context.Context, input io.Reader, output io.Writer, mode string) error {
	decoder := json.NewDecoder(input)
	var meta []json.RawMessage
	if decoder.Decode(&meta) != nil || len(meta) != 5 {
		return invalid
	}
	var kind, work, receivedMode string
	var version int
	var user *string
	if json.Unmarshal(meta[0], &kind) != nil || kind != "meta" || json.Unmarshal(meta[1], &version) != nil || version != 1 || json.Unmarshal(meta[2], &work) != nil || work != "sc" || json.Unmarshal(meta[3], &receivedMode) != nil || receivedMode != mode || (mode != "list" && mode != "latest") || json.Unmarshal(meta[4], &user) != nil {
		return invalid
	}
	if mode == "list" && (user == nil || *user == "") || mode == "latest" && user != nil {
		return invalid
	}
	if _, err := io.WriteString(output, "["); err != nil {
		return err
	}
	count := 0
	seen := map[string]bool{}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		var row []json.RawMessage
		if decoder.Decode(&row) != nil || len(row) == 0 || json.Unmarshal(row[0], &kind) != nil {
			return invalid
		}
		if kind == "end" {
			if len(row) != 1 {
				return invalid
			}
			var trailing json.RawMessage
			if decoder.Decode(&trailing) != io.EOF {
				return invalid
			}
			_, err := io.WriteString(output, "]")
			return err
		}
		if kind != "row" || len(row) != 4 {
			return invalid
		}
		var conversation summaryConversation
		if decodeFields(row[1], &conversation, "id", "character_id", "created_at", "title") != nil || seen[conversation.ID] {
			return invalid
		}
		seen[conversation.ID] = true
		var flag *bool
		if json.Unmarshal(row[2], &flag) != nil || flag == nil {
			return invalid
		}
		var candidates previewCandidates
		if decodeFields(row[3], &candidates, "message", "event", "reach_out", "last_turn_key", "ordering_error") != nil {
			return invalid
		}
		var fields map[string]json.RawMessage
		json.Unmarshal(row[3], &fields)
		if candidates.Message != nil {
			if decodeFields(fields["message"], candidates.Message, "id", "role", "text", "ja", "zh", "created_at", "photo_status", "display_error") != nil {
				return invalid
			}
		}
		if candidates.Event != nil {
			if decodeFields(fields["event"], candidates.Event, "id", "kind", "created_at", "gift_id", "gift_name", "order_key") != nil {
				return invalid
			}
		}
		if candidates.ReachOut != nil {
			if decodeFields(fields["reach_out"], candidates.ReachOut, "id", "ja", "zh", "created_at", "display_error", "order_key") != nil {
				return invalid
			}
		}
		preview, err := lastPreview(candidates)
		if err != nil {
			return err
		}
		var shown map[string]any
		if mode == "list" {
			shown = map[string]any{"id": conversation.ID, "user_id": *user, "character_id": conversation.Character, "created_at": conversation.CreatedAt, "title": conversation.Title, "last_message": preview, "ended": *flag}
		} else {
			shown = map[string]any{"character_id": conversation.Character, "conversation_id": conversation.ID, "created_at": conversation.CreatedAt, "last_message": preview, "unseen_reach_out": *flag}
		}
		if count > 0 {
			if _, err = io.WriteString(output, ","); err != nil {
				return err
			}
		}
		count++
		if err = json.NewEncoder(output).Encode(shown); err != nil {
			return err
		}
	}
}

func decodeFields(data []byte, dest any, keys ...string) error {
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil || len(fields) != len(keys) {
		return invalid
	}
	for _, key := range keys {
		if _, ok := fields[key]; !ok {
			return invalid
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(dest) != nil {
		return invalid
	}
	return nil
}

func validOrder(key *comparisonOrder) bool {
	return key != nil && key.Micros != nil && key.Aware != nil
}
func compareOrder(a, b *comparisonOrder) (int, error) {
	if !validOrder(a) || !validOrder(b) {
		return 0, invalid
	}
	if *a.Aware != *b.Aware {
		return 0, legacyPreviewFailure
	}
	return cmp.Compare(*a.Micros, *b.Micros), nil
}
func lastPreview(c previewCandidates) (any, error) {
	if c.OrderingError {
		return nil, legacyPreviewFailure
	}
	pending := ""
	var at *comparisonOrder
	if c.Event != nil {
		if !validOrder(c.Event.Order) {
			return nil, invalid
		}
		pending = "event"
		at = c.Event.Order
	}
	if c.ReachOut != nil {
		if !validOrder(c.ReachOut.Order) {
			return nil, invalid
		}
		if c.Event == nil {
			pending = "reach_out"
			at = c.ReachOut.Order
		} else {
			compared, err := compareOrder(c.ReachOut.Order, c.Event.Order)
			if err != nil {
				return nil, err
			}
			if compared >= 0 {
				pending = "reach_out"
				at = c.ReachOut.Order
			}
		}
	}
	choosePending := pending != ""
	if choosePending && c.Message != nil {
		compared, err := compareOrder(at, c.Said)
		if err != nil {
			return nil, err
		}
		choosePending = compared >= 0
	}
	if choosePending {
		if pending == "event" {
			event := c.Event
			shown := map[string]any{"type": "event", "kind": event.Kind, "created_at": event.At}
			if event.Gift != nil {
				if event.Name == nil {
					return nil, unknownGift
				}
				shown["gift_id"] = *event.Gift
				shown["gift_name"] = *event.Name
			}
			return shown, nil
		}
		r := c.ReachOut
		if r.DisplayError {
			return nil, legacyPreviewFailure
		}
		return map[string]any{"role": "character", "text": nil, "ja": r.Ja, "zh": r.Zh, "created_at": r.At, "reach_out": true}, nil
	}
	if c.Message == nil {
		return nil, nil
	}
	m := c.Message
	if m.DisplayError {
		return nil, legacyPreviewFailure
	}
	shown := map[string]any{"role": m.Role, "text": m.Text, "ja": m.Ja, "zh": m.Zh, "created_at": m.At}
	if m.PhotoStatus != nil && *m.PhotoStatus == "ready" {
		shown["photo"] = true
	}
	return shown, nil
}
