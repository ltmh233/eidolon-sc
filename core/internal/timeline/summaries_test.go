// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestSummarySelectsReachOutAtTieAndKeepsMessageBeforeOlderPending(t *testing.T) {
	meta := `["meta",1,"sc","list","user"]` + "\n"
	row := `["row",{"id":"c","character_id":"mano","created_at":"2026-10-28T00:00:00Z","title":null},true,{"message":{"id":9,"role":"character","text":null,"ja":"reply","zh":"回复","created_at":"2026-10-28T12:02:00Z","photo_status":"ready","display_error":false},"event":{"id":3,"kind":"hung_up","created_at":"2026-10-28T12:00:00Z","gift_id":null,"gift_name":null,"order_key":{"micros":1793188800000000,"aware":true}},"reach_out":{"id":1,"ja":"reach","zh":"搭话","display_error":false,"order_key":{"micros":1793188800000000,"aware":true},"created_at":"2026-10-28T12:00:00Z"},"ordering_error":false,"last_turn_key":{"micros":1793188800000000,"aware":true}}]`
	for _, c := range []struct {
		name, raw string
		reach     bool
	}{
		{"same time reach out wins", row, true},
		{"pending before latest user cannot replace reply", strings.Replace(row, `"last_turn_key":{"micros":1793188800000000,"aware":true}`, `"last_turn_key":{"micros":1793188860000000,"aware":true}`, 1), false},
	} {
		var output strings.Builder
		if err := ProjectSummaries(context.Background(), strings.NewReader(meta+c.raw+"\n[\"end\"]\n"), &output, "list"); err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		var public []map[string]any
		if err := json.Unmarshal([]byte(output.String()), &public); err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		preview := public[0]["last_message"].(map[string]any)
		if c.reach && preview["reach_out"] != true || !c.reach && preview["photo"] != true {
			t.Fatalf("%s: %s", c.name, output.String())
		}
		if _, ok := preview["id"]; ok {
			t.Fatalf("%s: private record id exposed", c.name)
		}
	}
}
