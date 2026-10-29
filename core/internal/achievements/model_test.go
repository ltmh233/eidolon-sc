// SPDX-License-Identifier: AGPL-3.0-or-later
package achievements_test

import (
	"encoding/json"
	"io"
	"strings"
	"testing"

	"eidolon/core/internal/achievements"
)

const meta = `["meta",1,"sc","mano",true,1,1,1,["py311-sql-v1",4300]]`
const rules = `["rules",["close","intimate"],"1"]`
const definition = `["achievement","turns",[["10","1"]]]`
const count = `["count",["s","turns"],["i","9007199254740993"]]`
const stage = `["stage",["s","turns"],["i","1"],["2026-10-29T00:00:00+00:00",true,"1793232000000000"]]`

func stream(rows ...string) string { return strings.Join(rows, "\n") + "\n" }
func valid() string                { return stream(meta, rules, definition, count, stage, `["end"]`) }

func TestComputeSavedFactsAndStableCompletion(t *testing.T) {
	source := stream(`["meta",1,"sc","mano",true,2,3,3,["py311-sql-v1",4300]]`, rules,
		`["achievement","tier",[["1","8"]]]`, definition,
		`["count",["s","tier"],["f","bffe666666666666"]]`, count, `["count",["s","complete"],["b","YmFk"]]`,
		stage, `["stage",["s","tier"],["f","3ff0000000000000"],["2026-10-29T09:00:00+09:00",true,"1793232000000000"]]`,
		`["stage",["b","dHVybnM="],["s","1"],["2026-10-30T00:00:00",false,"1793318400000000"]]`, `["end"]`)
	s, e := achievements.Decode(strings.NewReader(source), "mano")
	if e != nil {
		t.Fatal(e)
	}
	result, e := achievements.Compute(s)
	if e != nil {
		t.Fatal(e)
	}
	data, e := json.Marshal(result)
	if e != nil {
		t.Fatal(e)
	}
	expected := `{"achievements":[{"id":"tier","progress":"close","stages":[{"goal":"close","draws":8,"unlocked_at":"2026-10-29T09:00:00+09:00"}]},{"id":"turns","progress":9007199254740993,"stages":[{"goal":10,"draws":1,"unlocked_at":"2026-10-29T00:00:00+00:00"}]}],"complete":{"completed_at":"2026-10-29T09:00:00+09:00","reset_cards":0}}`
	if string(data) != expected {
		t.Fatalf("public complete shape mismatch: %s", data)
	}
}

func TestWireIntegrityPrecedesLegacySemanticErrors(t *testing.T) {
	// This count is valid wire but fails int; an absent end must still be wire failure.
	badInt := strings.Replace(valid(), `["i","9007199254740993"]`, `["s","bad"]`, 1)
	s, e := achievements.Decode(strings.NewReader(badInt), "mano")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = achievements.Compute(s); e != achievements.ErrLegacy {
		t.Fatalf("selected invalid int: %v", e)
	}
	if _, e = achievements.Decode(strings.NewReader(strings.TrimSuffix(badInt, "[\"end\"]\n")), "mano"); e != achievements.ErrContract {
		t.Fatalf("truncated stream: %v", e)
	}
}

func TestContractRejectsMalformedStreams(t *testing.T) {
	cases := map[string]string{
		"wrong version":                 strings.Replace(valid(), `"meta",1,`, `"meta",2,`, 1),
		"wrong scope":                   strings.Replace(valid(), `"sc"`, `"demo"`, 1),
		"wrong character":               strings.Replace(valid(), `"mano"`, `"kogane"`, 1),
		"extra meta":                    strings.Replace(valid(), `,4300]]`, `,4300],0]`, 1),
		"false size":                    strings.Replace(valid(), `true,1,1,1,`, `true,false,1,1,`, 1),
		"null size":                     strings.Replace(valid(), `true,1,1,1,`, `true,null,1,1,`, 1),
		"fraction size":                 strings.Replace(valid(), `true,1,1,1,`, `true,1.0,1,1,`, 1),
		"overflow size":                 strings.Replace(valid(), `true,1,1,1,`, `true,9223372036854775808,1,1,`, 1),
		"profile":                       strings.Replace(valid(), `py311-sql-v1`, `py314-sql-v1`, 1),
		"limit":                         strings.Replace(valid(), `4300`, `639`, 1),
		"profile false":                 strings.Replace(valid(), `4300`, `false`, 1),
		"missing count":                 stream(meta, rules, definition, stage, `["end"]`),
		"truncated":                     strings.TrimSuffix(valid(), "\n"),
		"no end":                        stream(meta, rules, definition, count, stage),
		"trailing":                      valid() + "\n",
		"two values":                    strings.Replace(valid(), meta, meta+` []`, 1),
		"empty line":                    "\n" + valid(),
		"unknown row":                   strings.Replace(valid(), `"count"`, `"counts"`, 1),
		"wrong order":                   stream(meta, rules, count, definition, stage, `["end"]`),
		"duplicate definition":          stream(strings.Replace(meta, `true,1,1,1`, `true,2,1,1`, 1), rules, definition, definition, count, stage, `["end"]`),
		"reserved rule":                 strings.Replace(valid(), `"achievement","turns"`, `"achievement","complete"`, 1),
		"empty rule":                    strings.Replace(valid(), `"achievement","turns"`, `"achievement",""`, 1),
		"object rule":                   strings.Replace(valid(), `[["10","1"]]`, `{"goal":"10","goal":"10"}`, 1),
		"dec sign":                      strings.Replace(valid(), `"10"`, `"+10"`, 1),
		"dec zero":                      strings.Replace(valid(), `"10"`, `"01"`, 1),
		"dec negative zero":             strings.Replace(valid(), `"10"`, `"-0"`, 1),
		"integer overflow":              strings.Replace(valid(), `9007199254740993`, `9223372036854775808`, 1),
		"float uppercase":               strings.Replace(valid(), `["i","9007199254740993"]`, `["f","3FF0000000000000"]`, 1),
		"base64 noncanonical":           strings.Replace(valid(), `["i","9007199254740993"]`, `["b","YR=="]`, 1),
		"key numeric":                   strings.Replace(valid(), `["s","turns"]`, `["i","1"]`, 1),
		"lone surrogate":                strings.Replace(valid(), `"turns"`, `"\ud800"`, 1),
		"invalid UTF8":                  strings.Replace(valid(), `"turns"`, "\"\xff\"", 1),
		"NaN token":                     strings.Replace(valid(), `["i","9007199254740993"]`, `["f",NaN]`, 1),
		"time micros":                   strings.Replace(valid(), `1793232000000000`, `1793232000000001`, 1),
		"time aware":                    strings.Replace(valid(), `true,"1793232000000000"`, `false,"1793232000000000"`, 1),
		"time noncanonical":             strings.Replace(valid(), `T00:00:00+00:00`, `T00:00:00Z`, 1),
		"time bad day":                  strings.Replace(valid(), `2026-10-29T`, `2026-02-30T`, 1),
		"time zero fraction":            strings.Replace(valid(), `T00:00:00+`, `T00:00:00.000000+`, 1),
		"time negative zero offset":     strings.Replace(valid(), `+00:00`, `-00:00`, 1),
		"disabled extra":                strings.Replace(valid(), `true,1,1,1,["py311-sql-v1",4300]`, `false,0,0,0,null`, 1),
		"duplicate count":               stream(strings.Replace(meta, `true,1,1,1`, `true,1,2,1`, 1), rules, definition, count, count, stage, `["end"]`),
		"duplicate numeric stage alias": stream(strings.Replace(meta, `true,1,1,1`, `true,1,1,2`, 1), rules, definition, count, stage, strings.Replace(stage, `["i","1"]`, `["f","3ff0000000000000"]`, 1), `["end"]`),
	}
	for name, source := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := achievements.Decode(strings.NewReader(source), "mano"); err != achievements.ErrContract {
				t.Fatalf("want wire error, got %v", err)
			}
		})
	}
}

func TestSparseExtraFactsAndDisabledSnapshots(t *testing.T) {
	cases := map[string]string{
		"disabled":              stream(`["meta",1,"sc","mano",false,0,0,0,null]`, `["end"]`),
		"empty":                 stream(`["meta",1,"sc","mano",true,0,0,0,["py311-sql-v1",0]]`, rules, `["end"]`),
		"sparse":                stream(`["meta",1,"sc","mano",true,1,0,0,["py311-sql-v1",4300]]`, rules, definition, `["end"]`),
		"extra fractional key":  strings.Replace(valid(), `["i","1"]`, `["f","3ff8000000000000"]`, 1),
		"extra infinite key":    strings.Replace(valid(), `["i","1"]`, `["f","7ff0000000000000"]`, 1),
		"unused infinite count": strings.Replace(valid(), `["s","turns"],["i","9007199254740993"]`, `["s","old"],["f","7ff0000000000000"]`, 1),
		"long text":             strings.Replace(valid(), `["i","9007199254740993"]`, `["s","`+strings.Repeat("0", 70000)+`"]`, 1),
	}
	for name, source := range cases {
		t.Run(name, func(t *testing.T) {
			s, e := achievements.Decode(strings.NewReader(source), "mano")
			if e != nil {
				t.Fatal(e)
			}
			_, e = achievements.Compute(s)
			if name == "long text" {
				if e != achievements.ErrLegacy {
					t.Fatalf("long selected int: %v", e)
				}
			} else if e != nil {
				t.Fatal(e)
			}
		})
	}
}

type failingReader struct{}

func (failingReader) Read([]byte) (int, error) { return 0, io.ErrUnexpectedEOF }
func TestReadFailure(t *testing.T) {
	if _, e := achievements.Decode(io.MultiReader(strings.NewReader(meta+"\n"), failingReader{}), "mano"); e != achievements.ErrContract {
		t.Fatal(e)
	}
}

func TestRuleDecimalSerializationLimit(t *testing.T) {
	for _, c := range []struct {
		name  string
		limit string
		value string
		valid bool
	}{
		{"boundary", "4300", strings.Repeat("9", 4300), true},
		{"negative sign excluded", "4300", "-" + strings.Repeat("9", 4300), true},
		{"too many digits", "4300", strings.Repeat("9", 4301), false},
		{"disabled limit", "0", strings.Repeat("9", 4301), true},
	} {
		for _, field := range []string{"cards", "goal", "draws"} {
			t.Run(c.name+"/"+field, func(t *testing.T) {
				source := strings.Replace(valid(), "4300", c.limit, 1)
				switch field {
				case "cards":
					source = strings.Replace(source, rules, `["rules",["close","intimate"],"`+c.value+`"]`, 1)
				case "goal":
					source = strings.Replace(source, `[["10","1"]]`, `[["`+c.value+`","1"]]`, 1)
				case "draws":
					source = strings.Replace(source, `[["10","1"]]`, `[["10","`+c.value+`"]]`, 1)
				}
				_, err := achievements.Decode(strings.NewReader(source), "mano")
				if c.valid && err != nil {
					t.Fatal(err)
				}
				if !c.valid && err != achievements.ErrContract {
					t.Fatalf("unserializable rule must be wire error, got %v", err)
				}
			})
		}
	}
}
