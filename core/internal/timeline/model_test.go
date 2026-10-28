// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestTurnPairsAndEventReachOutTieOrdering(t *testing.T) {
	raw := `["meta",1,"sc","conversation"]
["message",{"id":2,"role":"character","text":null,"ja":"返事","zh":"回复","created_at":"2026-10-28T12:02:00+00:00","segments":[],"affinity":null,"photo":{"id":"p","status":"ready","reason":null,"tag":"PRIVATE_TAG"}}]
["message",{"id":1,"role":"user","text":"你好","ja":null,"zh":null,"created_at":"2026-10-28T12:00:00+00:00","segments":[],"affinity":null,"photo":null}]
["event",{"id":5,"kind":"hung_up","created_at":"2026-10-28T12:01:00+00:00","affinity":null,"gift_id":null,"liked":false,"type":"event"},null]
["reach_out",{"id":1,"ja":"先说","zh":"搭话","segments":[],"created_at":"2026-10-28T12:01:00+00:00","type":"reach_out"}]
["end"]
`
	var output strings.Builder
	if err := Project(context.Background(), strings.NewReader(raw), &output, "conversation"); err != nil {
		t.Fatal(err)
	}
	var shown []map[string]any
	if err := json.Unmarshal([]byte(output.String()), &shown); err != nil {
		t.Fatal(err)
	}
	if len(shown) != 4 || shown[0]["role"] != "user" || shown[1]["role"] != "character" || shown[2]["type"] != "event" || shown[3]["type"] != "reach_out" {
		t.Fatal("turn/tie order", output.String())
	}
	if strings.Contains(output.String(), "PRIVATE_TAG") || strings.Contains(output.String(), "liked") || strings.Contains(output.String(), "gift_id") {
		t.Fatal("private fields exposed")
	}
}

func TestMalformedPrivatePhotoCannotBecomePublicObject(t *testing.T) {
	raw := `["meta",1,"sc","c"]
["message",{"id":1,"role":"character","text":null,"ja":"a","zh":"b","created_at":"2026-10-28T00:00:00Z","segments":[],"affinity":null,"photo":{"id":"p","status":{"private":42},"reason":null,"tag":"secret"}}]
["end"]`
	var output strings.Builder
	if err := Project(context.Background(), strings.NewReader(raw), &output, "c"); err == nil || output.Len() != 0 {
		t.Fatal("malformed private photo exposed")
	}
}
