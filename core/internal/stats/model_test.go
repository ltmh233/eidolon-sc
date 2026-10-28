// SPDX-License-Identifier: AGPL-3.0-or-later
package stats

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestCalendarAggregationAndTrustedCharacterOrder(t *testing.T) {
	raw := `["meta",1,"sc","30d","2026-03-09T00:30:00-04:00","2026-03-01T23:00:00-05:00",true,{"stages":2,"complete":0}]
["character","mano",null]
["character","kogane",null]
["first",0,"2026-01-01T12:00:00-05:00"]
["first",1,"2026-03-08T00:00:00-05:00"]
["contact",0,"turn","2026-03-08T01:30:00-05:00",false]
["contact",0,"gift","2026-03-09T00:00:00-04:00",true]
["contact",1,"gift","2026-03-08T03:30:00-04:00",false]
["contact",-1,"turn","2026-01-01T12:00:00-05:00",false]
["call",0,"2026-03-08T12:00:00-04:00",12.5]
["end"]
`
	got, err := Compute(strings.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	// Expectations worked from the dates above, independently of the implementation.
	encoded, _ := json.Marshal(got)
	var public map[string]any
	json.Unmarshal(encoded, &public)
	if public["days_known"] != float64(68) || public["visits"].(map[string]any)["current"] != float64(2) {
		t.Fatalf("calendar: %s", encoded)
	}
	span := public["range"].(map[string]any)
	if span["turns"] != float64(1) || span["gifts"].(map[string]any)["most"].(map[string]any)["character_id"] != "mano" {
		t.Fatalf("counts/tie: %s", encoded)
	}
	if span["calls"].(map[string]any)["seconds"] != 12.5 {
		t.Fatalf("call: %s", encoded)
	}
}
