// SPDX-License-Identifier: AGPL-3.0-or-later
// Package achievements projects saved SC achievement facts, never rewards them.
package achievements

import (
	"bufio"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

var ErrContract = errors.New("invalid SC achievement contract")
var ErrLegacy = errors.New("legacy SC achievement projection failed")
var decimal = regexp.MustCompile(`^(0|-?[1-9][0-9]*)$`)
var natural = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)
var bitsPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)

type Scalar struct{ kind, value string }
type Time struct {
	ISO    string
	Aware  bool
	Micros int64
}
type Stage struct{ Goal, Draws *big.Int }
type Definition struct {
	ID     string
	Stages []Stage
}
type Snapshot struct {
	Enabled     bool
	Digits      int64
	Tiers       []string
	Cards       *big.Int
	Definitions []Definition
	Counts      map[string]Scalar
	Unlocked    map[string]map[string]Time
}

// Decode validates the entire authenticated stream, including end and EOF, before Compute.
func Decode(source io.Reader, character string) (Snapshot, error) {
	s := Snapshot{Counts: map[string]Scalar{}, Unlocked: map[string]map[string]Time{}}
	r := bufio.NewReader(source)
	next := func() ([]any, error) {
		line, err := r.ReadBytes('\n')
		if err != nil {
			return nil, ErrContract
		}
		if !validJSONText(line) {
			return nil, ErrContract
		}
		d := json.NewDecoder(bytes.NewReader(line))
		d.UseNumber()
		var row []any
		if d.Decode(&row) != nil || row == nil {
			return nil, ErrContract
		}
		var trailing any
		if d.Decode(&trailing) != io.EOF {
			return nil, ErrContract
		}
		return row, nil
	}
	row, err := next()
	if err != nil || len(row) != 9 || row[0] != "meta" || row[1] != json.Number("1") || row[2] != "sc" || row[3] != character {
		return s, ErrContract
	}
	enabled, ok := row[4].(bool)
	if !ok {
		return s, ErrContract
	}
	s.Enabled = enabled
	n, e1 := size(row[5])
	nc, e2 := size(row[6])
	ns, e3 := size(row[7])
	if e1 != nil || e2 != nil || e3 != nil {
		return s, ErrContract
	}
	if !enabled {
		if n != 0 || nc != 0 || ns != 0 || row[8] != nil {
			return s, ErrContract
		}
	} else {
		profile, ok := row[8].([]any)
		if !ok || len(profile) != 2 || profile[0] != "py311-sql-v1" {
			return s, ErrContract
		}
		s.Digits, err = size(profile[1])
		if err != nil || (s.Digits != 0 && s.Digits < 640) {
			return s, ErrContract
		}
		row, err = next()
		if err != nil || len(row) != 3 || row[0] != "rules" {
			return s, ErrContract
		}
		names, ok := row[1].([]any)
		if !ok {
			return s, ErrContract
		}
		s.Tiers = make([]string, len(names))
		for i, v := range names {
			name, ok := v.(string)
			if !ok {
				return s, ErrContract
			}
			s.Tiers[i] = name
		}
		s.Cards, err = ruleInteger(row[2], s.Digits)
		if err != nil {
			return s, err
		}
		ids := map[string]bool{}
		for range n {
			row, err = next()
			if err != nil || len(row) != 3 || row[0] != "achievement" {
				return s, ErrContract
			}
			id, ok := row[1].(string)
			if !ok || id == "" || id == "complete" || ids[id] {
				return s, ErrContract
			}
			ids[id] = true
			stages, ok := row[2].([]any)
			if !ok {
				return s, ErrContract
			}
			def := Definition{ID: id, Stages: make([]Stage, 0, len(stages))}
			for _, v := range stages {
				pair, ok := v.([]any)
				if !ok || len(pair) != 2 {
					return s, ErrContract
				}
				goal, e1 := ruleInteger(pair[0], s.Digits)
				draws, e2 := ruleInteger(pair[1], s.Digits)
				if e1 != nil || e2 != nil {
					return s, ErrContract
				}
				def.Stages = append(def.Stages, Stage{goal, draws})
			}
			s.Definitions = append(s.Definitions, def)
		}
		for range nc {
			row, err = next()
			if err != nil || len(row) != 3 || row[0] != "count" {
				return s, ErrContract
			}
			key, e1 := scalar(row[1], true)
			value, e2 := scalar(row[2], false)
			if e1 != nil || e2 != nil {
				return s, ErrContract
			}
			k := key.identity()
			if _, exists := s.Counts[k]; exists {
				return s, ErrContract
			}
			s.Counts[k] = value
		}
		for i := int64(0); i < ns; i++ {
			row, err = next()
			if err != nil || len(row) != 4 || row[0] != "stage" {
				return s, ErrContract
			}
			key, e1 := scalar(row[1], true)
			stage, e2 := scalar(row[2], false)
			at, e3 := decodeTime(row[3])
			if e1 != nil || e2 != nil || e3 != nil {
				return s, ErrContract
			}
			k := key.identity()
			if s.Unlocked[k] == nil {
				s.Unlocked[k] = map[string]Time{}
			}
			index := stage.identity()
			if stage.kind == "f" && math.IsNaN(stage.float()) {
				index += ":" + strconv.FormatInt(i, 10)
			}
			if _, exists := s.Unlocked[k][index]; exists {
				return s, ErrContract
			}
			s.Unlocked[k][index] = at
		}
	}
	row, err = next()
	if err != nil || len(row) != 1 || row[0] != "end" {
		return s, ErrContract
	}
	if _, err = r.ReadByte(); err != io.EOF {
		return s, ErrContract
	}
	return s, nil
}

func size(v any) (int64, error) {
	n, ok := v.(json.Number)
	if !ok || !natural.MatchString(string(n)) {
		return 0, ErrContract
	}
	x, e := strconv.ParseInt(string(n), 10, 64)
	if e != nil {
		return 0, ErrContract
	}
	return x, nil
}
func integer(v any) (*big.Int, error) {
	t, ok := v.(string)
	if !ok || !decimal.MatchString(t) {
		return nil, ErrContract
	}
	n, ok := new(big.Int).SetString(t, 10)
	if !ok {
		return nil, ErrContract
	}
	return n, nil
}

// Only rule integers must already be serializable by the authenticated Python profile.
// Raw SQL TEXT counts retain lazy conversion, including their own semantic failures.
func ruleInteger(v any, limit int64) (*big.Int, error) {
	n, err := integer(v)
	if err != nil {
		return nil, err
	}
	text := v.(string)
	if strings.HasPrefix(text, "-") {
		text = text[1:]
	}
	if limit != 0 && int64(len(text)) > limit {
		return nil, ErrContract
	}
	return n, nil
}

func scalar(v any, key bool) (Scalar, error) {
	a, ok := v.([]any)
	if !ok || len(a) != 2 {
		return Scalar{}, ErrContract
	}
	k, ok := a[0].(string)
	if !ok {
		return Scalar{}, ErrContract
	}
	val, ok := a[1].(string)
	if !ok {
		return Scalar{}, ErrContract
	}
	s := Scalar{k, val}
	if key && k != "s" && k != "b" {
		return s, ErrContract
	}
	switch k {
	case "i":
		n, e := integer(val)
		if e != nil || !n.IsInt64() {
			return s, ErrContract
		}
	case "f":
		if !bitsPattern.MatchString(val) {
			return s, ErrContract
		}
	case "s":
	case "b":
		raw, e := base64.StdEncoding.DecodeString(val)
		if e != nil || base64.StdEncoding.EncodeToString(raw) != val {
			return s, ErrContract
		}
	default:
		return s, ErrContract
	}
	return s, nil
}
func (s Scalar) float() float64 {
	n, _ := strconv.ParseUint(s.value, 16, 64)
	return math.Float64frombits(n)
}
func (s Scalar) identity() string {
	switch s.kind {
	case "i":
		return "n:" + s.value
	case "f":
		f := s.float()
		if !math.IsInf(f, 0) && !math.IsNaN(f) {
			return "n:" + new(big.Rat).SetFloat64(f).RatString()
		}
	}
	return s.kind + ":" + s.value
}

// validJSONText rejects invalid UTF-8 and lone surrogate escapes, which encoding/json replaces.
// Row schemas reject all objects; errorObject separately checks its exact keys and duplicates.
func validJSONText(b []byte) bool {
	if !utf8.Valid(b) {
		return false
	}
	quoted := false
	for i := 0; i < len(b); i++ {
		c := b[i]
		if !quoted {
			if c == '"' {
				quoted = true
			}
			continue
		}
		if c == '"' {
			quoted = false
			continue
		}
		if c != '\\' {
			continue
		}
		i++
		if i >= len(b) {
			return false
		}
		if b[i] != 'u' {
			continue
		}
		if i+4 >= len(b) {
			return false
		}
		n, e := strconv.ParseUint(string(b[i+1:i+5]), 16, 16)
		if e != nil {
			return false
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(b) || b[i+1] != '\\' || b[i+2] != 'u' {
				return false
			}
			low, e := strconv.ParseUint(string(b[i+3:i+7]), 16, 16)
			if e != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		}
	}
	return true
}

var normalizedTime = regexp.MustCompile(`^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]{6})?([+-][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{6})?)?)?$`)

func decodeTime(v any) (Time, error) {
	a, ok := v.([]any)
	if !ok || len(a) != 3 {
		return Time{}, ErrContract
	}
	iso, ok := a[0].(string)
	if !ok {
		return Time{}, ErrContract
	}
	aware, ok := a[1].(bool)
	if !ok {
		return Time{}, ErrContract
	}
	micros, e := integer(a[2])
	if e != nil || !micros.IsInt64() {
		return Time{}, ErrContract
	}
	m := normalizedTime.FindStringSubmatch(iso)
	if m == nil || aware != (m[8] != "") {
		return Time{}, ErrContract
	}
	num := func(i int) int { n, _ := strconv.Atoi(m[i]); return n }
	y, mo, day, h, mi, sec := num(1), num(2), num(3), num(4), num(5), num(6)
	us := 0
	if m[7] != "" {
		us, _ = strconv.Atoi(m[7][1:])
		if us == 0 {
			return Time{}, ErrContract
		}
	}
	if y < 1 || mo < 1 || mo > 12 || day < 1 || day > 31 || h > 23 || mi > 59 || sec > 59 {
		return Time{}, ErrContract
	}
	t := time.Date(y, time.Month(mo), day, h, mi, sec, us*1000, time.UTC)
	if t.Year() != y || int(t.Month()) != mo || t.Day() != day {
		return Time{}, ErrContract
	}
	offset := int64(0)
	if aware {
		off := m[8]
		oh, _ := strconv.Atoi(off[1:3])
		om, _ := strconv.Atoi(off[4:6])
		if oh > 23 || om > 59 {
			return Time{}, ErrContract
		}
		offset = int64(oh*3600+om*60) * 1000000
		if len(off) > 6 {
			os, _ := strconv.Atoi(off[7:9])
			if os > 59 {
				return Time{}, ErrContract
			}
			ous := 0
			if len(off) > 9 {
				ous, _ = strconv.Atoi(off[10:])
				if ous == 0 {
					return Time{}, ErrContract
				}
			}
			if os == 0 && ous == 0 {
				return Time{}, ErrContract
			}
			offset += int64(os)*1000000 + int64(ous)
		}
		if off[0] == '-' {
			if offset == 0 {
				return Time{}, ErrContract
			}
			offset = -offset
		}
	}
	actual := t.Unix()*1000000 + int64(us) - offset
	if actual != micros.Int64() {
		return Time{}, ErrContract
	}
	return Time{iso, aware, actual}, nil
}

type ShownStage struct {
	Goal       any      `json:"goal"`
	Draws      *big.Int `json:"draws"`
	UnlockedAt *string  `json:"unlocked_at"`
}
type Progress struct {
	ID       string       `json:"id"`
	Progress any          `json:"progress"`
	Stages   []ShownStage `json:"stages"`
}
type Complete struct {
	CompletedAt *string  `json:"completed_at"`
	ResetCards  *big.Int `json:"reset_cards"`
}
type Hers struct {
	Achievements []Progress `json:"achievements"`
	Complete     Complete   `json:"complete"`
}

// Compute runs coverage/max before the original ordered int/tier presentation.
func Compute(s Snapshot) (*Hers, error) {
	if !s.Enabled {
		return nil, nil
	}
	covered := true
	var needed []Time
	for _, def := range s.Definitions {
		for i := range def.Stages {
			at, ok := s.Unlocked["s:"+def.ID]["n:"+strconv.Itoa(i+1)]
			if !ok {
				covered = false
			}
			needed = append(needed, at)
		}
	}
	var completed *string
	if covered && len(needed) > 0 {
		latest := needed[0]
		for _, at := range needed[1:] {
			if at.Aware != latest.Aware {
				return nil, ErrLegacy
			}
			if at.Micros > latest.Micros {
				latest = at
			}
		}
		completed = &latest.ISO
	}
	cards := s.Cards
	if _, ok := s.Counts["s:complete"]; ok {
		cards = big.NewInt(0)
	}
	result := &Hers{Achievements: make([]Progress, 0, len(s.Definitions)), Complete: Complete{completed, cards}}
	for _, def := range s.Definitions {
		scalar, ok := s.Counts["s:"+def.ID]
		if !ok {
			scalar = Scalar{"i", "0"}
		}
		n, err := scalar.pythonInt(s.Digits)
		if err != nil {
			return nil, ErrLegacy
		}
		var progress any = n
		if def.ID == "tier" {
			progress, err = tier(n, s.Tiers)
			if err != nil {
				return nil, err
			}
		}
		p := Progress{ID: def.ID, Progress: progress, Stages: make([]ShownStage, 0, len(def.Stages))}
		for i, stage := range def.Stages {
			var goal any = stage.Goal
			if def.ID == "tier" {
				goal, err = tier(stage.Goal, s.Tiers)
				if err != nil {
					return nil, err
				}
			}
			var iso *string
			if at, ok := s.Unlocked["s:"+def.ID]["n:"+strconv.Itoa(i+1)]; ok {
				iso = &at.ISO
			}
			p.Stages = append(p.Stages, ShownStage{goal, stage.Draws, iso})
		}
		result.Achievements = append(result.Achievements, p)
	}
	return result, nil
}
func tier(rank *big.Int, names []string) (any, error) {
	if rank.Sign() == 0 {
		return nil, nil
	}
	j := new(big.Int).Sub(rank, big.NewInt(1))
	if j.Sign() < 0 {
		j.Add(j, big.NewInt(int64(len(names))))
	}
	if !j.IsInt64() || j.Sign() < 0 || j.Int64() >= int64(len(names)) {
		return nil, ErrLegacy
	}
	return names[j.Int64()], nil
}

func (s Scalar) pythonInt(limit int64) (*big.Int, error) {
	switch s.kind {
	case "i":
		n, _ := new(big.Int).SetString(s.value, 10)
		return n, nil
	case "f":
		f := s.float()
		if math.IsNaN(f) || math.IsInf(f, 0) {
			return nil, ErrLegacy
		}
		n, _ := new(big.Float).SetFloat64(f).Int(nil)
		return n, nil
	case "s":
		return textInt(s.value, false, limit)
	case "b":
		raw, _ := base64.StdEncoding.DecodeString(s.value)
		return textInt(string(raw), true, limit)
	}
	return nil, ErrLegacy
}

// Unicode 14.0 Nd block starts; do not inherit the Go runtime's newer Unicode profile.
var digitStarts = []rune{0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66, 0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90, 0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0, 0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x16a60, 0x16ac0, 0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e950, 0x1fbf0}

func digit(r rune, ascii bool) (byte, bool) {
	if ascii {
		if r >= '0' && r <= '9' {
			return byte(r), true
		}
		return 0, false
	}
	for _, start := range digitStarts {
		if r >= start && r < start+10 {
			return byte('0' + r - start), true
		}
	}
	return 0, false
}
func intSpace(r rune, ascii bool) bool {
	if r >= 9 && r <= 13 || r == 32 {
		return true
	}
	return !ascii && (r == 0x85 || r == 0xa0 || r == 0x1680 || r >= 0x2000 && r <= 0x200a || r == 0x2028 || r == 0x2029 || r == 0x202f || r == 0x205f || r == 0x3000)
}
func textInt(text string, ascii bool, limit int64) (*big.Int, error) {
	text = strings.TrimFunc(text, func(r rune) bool { return intSpace(r, ascii) })
	sign := ""
	if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
		sign = text[:1]
		text = text[1:]
	}
	var digits strings.Builder
	previous := false
	count := int64(0)
	for _, r := range text {
		if r == '_' && previous {
			previous = false
			continue
		}
		d, ok := digit(r, ascii)
		if !ok {
			return nil, ErrLegacy
		}
		digits.WriteByte(d)
		count++
		previous = true
	}
	if !previous || count == 0 || limit != 0 && count > limit {
		return nil, ErrLegacy
	}
	n, ok := new(big.Int).SetString(sign+digits.String(), 10)
	if !ok {
		return nil, ErrLegacy
	}
	return n, nil
}
