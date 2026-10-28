// SPDX-License-Identifier: AGPL-3.0-or-later
// Package stats aggregates private SC metadata without accessing SQLite or rewards.
package stats

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"time"

	"eidolon/core/internal/affinity"
)

const maxRecord = 64 << 10
const lastDay = 3652059 // Python datetime.date's 9999-12-31, ordinal 1 is 0001-01-01.

var invalid = errors.New("invalid stats contract")

type character struct {
	id           string
	first        *time.Time
	turns, given int64
	seconds      float64
	tier         string
}

// Compute consumes every record and the explicit terminator before exposing a result.
// Retained state is O(distinct characters), not O(events); visits uses a fixed date bitset.
func Compute(input io.Reader) (map[string]any, error) {
	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 4096), maxRecord)
	var now, signed time.Time
	var span string
	var enabled bool
	var achievements map[string]int64
	var today, start, since int
	var chars []character
	seenIDs := map[string]bool{}
	var hours, rangeHours [24]int64
	var heat []int64
	var visited [(lastDay + 64) / 64]uint64
	unknownGifts := map[int]int64{}
	var turns, given, liked, callCount int64
	var seconds float64
	haveMeta, dataStarted, ended := false, false, false
	for scanner.Scan() {
		if ended {
			return nil, invalid
		}
		var row []json.RawMessage
		if json.Unmarshal(scanner.Bytes(), &row) != nil || len(row) == 0 {
			return nil, invalid
		}
		var kind string
		if json.Unmarshal(row[0], &kind) != nil {
			return nil, invalid
		}
		if !haveMeta && kind != "meta" {
			return nil, invalid
		}
		switch kind {
		case "meta":
			if haveMeta || len(row) != 8 {
				return nil, invalid
			}
			var version int
			var work, observed, registered string
			if unpack(row[:7], &version, &work, &span, &observed, &registered, &enabled) != nil {
				return nil, invalid
			}
			// meta has six fields before the nullable achievements result.
			if version != 1 || work != "sc" || (span != "all" && span != "30d" && span != "1y") {
				return nil, invalid
			}
			var err error
			now, err = parseTime(observed)
			if err != nil {
				return nil, err
			}
			signed, err = parseTime(registered)
			if err != nil {
				return nil, err
			}
			if !bytes.Equal(bytes.TrimSpace(row[7]), []byte("null")) {
				var fields map[string]json.RawMessage
				if json.Unmarshal(row[7], &fields) != nil {
					return nil, invalid
				}
				for _, value := range fields {
					if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
						return nil, invalid
					}
				}
				if json.Unmarshal(row[7], &achievements) != nil || len(achievements) != 2 {
					return nil, invalid
				}
				if _, ok := achievements["stages"]; !ok {
					return nil, invalid
				}
				if _, ok := achievements["complete"]; !ok {
					return nil, invalid
				}
			}
			today = ordinal(now)
			weekday := (int(time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC).Weekday()) + 6) % 7
			start = today - weekday - 364
			if start < 1 {
				return nil, invalid
			}
			heat = make([]int64, today-start+1)
			if span == "30d" {
				since = today - 29
			}
			if span == "1y" {
				since = today - 364
			}
			haveMeta = true
		case "character":
			if dataStarted || len(row) != 3 {
				return nil, invalid
			}
			var id string
			if json.Unmarshal(row[1], &id) != nil || id == "" || seenIDs[id] {
				return nil, invalid
			}
			var state *affinity.Stored
			if !bytes.Equal(bytes.TrimSpace(row[2]), []byte("null")) {
				var fields map[string]json.RawMessage
				if json.Unmarshal(row[2], &fields) != nil || len(fields) != 3 {
					return nil, invalid
				}
				for _, key := range []string{"value", "updated_at_us", "cooling"} {
					if v, ok := fields[key]; !ok || bytes.Equal(bytes.TrimSpace(v), []byte("null")) {
						return nil, invalid
					}
				}
				if json.Unmarshal(row[2], &state) != nil || !enabled {
					return nil, invalid
				}
			}
			status, err := affinity.Compute(affinity.Snapshot{Enabled: enabled, ObservedAt: now.UTC().Format(time.RFC3339Nano), State: state})
			if err != nil {
				return nil, err
			}
			c := character{id: id}
			if status != nil {
				c.tier = status.Tier
			}
			chars = append(chars, c)
			seenIDs[id] = true
		case "first", "contact", "call":
			dataStarted = true
			var index int
			if len(row) < 3 || json.Unmarshal(row[1], &index) != nil || index >= len(chars) {
				return nil, invalid
			}
			switch kind {
			case "first":
				var stamp string
				if unpack(row, &index, &stamp) != nil {
					return nil, invalid
				}
				at, err := parseTime(stamp)
				if err != nil {
					return nil, err
				}
				if at.Before(signed) {
					signed = at
				}
				if index >= 0 {
					if chars[index].first != nil {
						return nil, invalid
					}
					chars[index].first = &at
				}
			case "contact":
				var contact, stamp string
				var wasLiked bool
				if unpack(row, &index, &contact, &stamp, &wasLiked) != nil || (contact != "turn" && contact != "gift") {
					return nil, invalid
				}
				at, err := parseTime(stamp)
				if err != nil {
					return nil, err
				}
				day := ordinal(at)
				hours[at.Hour()]++
				visited[day/64] |= uint64(1) << uint(day%64)
				if day >= start && day <= today {
					heat[day-start]++
				}
				if since == 0 || day >= since {
					rangeHours[at.Hour()]++
					if contact == "turn" {
						turns++
						if index >= 0 {
							chars[index].turns++
						}
					} else {
						given++
						if wasLiked {
							liked++
						}
						if index >= 0 {
							chars[index].given++
						} else {
							unknownGifts[index]++
						}
					}
				}
			case "call":
				var stamp string
				var duration float64
				if unpack(row, &index, &stamp, &duration) != nil || math.IsNaN(duration) || math.IsInf(duration, 0) {
					return nil, invalid
				}
				at, err := parseTime(stamp)
				if err != nil {
					return nil, err
				}
				if since == 0 || ordinal(at) >= since {
					callCount++
					seconds += duration
					if index >= 0 {
						chars[index].seconds += duration
					}
				}
			}
		case "end":
			if len(row) != 1 {
				return nil, invalid
			}
			ended = true
		default:
			return nil, invalid
		}
	}
	if scanner.Err() != nil || !ended || !haveMeta {
		return nil, invalid
	}
	present := func(day int) bool {
		return day >= 1 && day <= lastDay && visited[day/64]&(uint64(1)<<uint(day%64)) != 0
	}
	longest, run := 0, 0
	for day := 1; day <= today; day++ {
		if present(day) {
			run++
			longest = max(longest, run)
		} else {
			run = 0
		}
	}
	current, end := 0, today
	if !present(end) {
		end--
	}
	for present(end) {
		current++
		end--
	}
	known := func(at time.Time) int { return max(1, today-ordinal(at)+1) }
	people := make([]map[string]any, 0)
	groups := []map[string]any{}
	for _, tier := range []string{"cold", "distant", "trust", "close", "intimate"} {
		ids := []string{}
		for _, c := range chars {
			if c.tier == tier {
				ids = append(ids, c.id)
			}
		}
		groups = append(groups, map[string]any{"tier": tier, "characters": ids})
	}
	var most any
	var maximum int64
	for _, count := range unknownGifts {
		maximum = max(maximum, count)
	}
	for _, c := range chars {
		maximum = max(maximum, c.given)
	}
	for _, c := range chars {
		if c.first != nil {
			people = append(people, map[string]any{"character_id": c.id, "turns": c.turns, "days_known": known(*c.first), "call_seconds": c.seconds})
			if maximum > 0 && c.given == maximum && most == nil {
				most = map[string]any{"character_id": c.id, "given": maximum}
			}
		}
	}

	var gifts, tiers, achievementsShown any
	if enabled {
		gifts = map[string]any{"given": given, "liked": liked, "most": most}
		tiers = groups
	}
	if achievements != nil {
		achievementsShown = achievements
	}
	return map[string]any{
		"days_known": known(signed), "visits": map[string]int{"current": current, "longest": longest}, "all_hours": hours,
		"heatmap":  map[string]any{"from": time.Unix(int64(start-719163)*86400, 0).UTC().Format("2006-01-02"), "counts": heat},
		"affinity": tiers, "achievements": achievementsShown,
		"range": map[string]any{"span": span, "turns": turns, "by_hour": rangeHours, "characters": people, "calls": map[string]any{"count": callCount, "seconds": seconds}, "gifts": gifts},
	}, nil
}

func unpack(row []json.RawMessage, dest ...any) error {
	if len(row) != len(dest)+1 {
		return invalid
	}
	for i, d := range dest {
		if bytes.Equal(bytes.TrimSpace(row[i+1]), []byte("null")) || json.Unmarshal(row[i+1], d) != nil {
			return invalid
		}
	}
	return nil
}

func parseTime(value string) (time.Time, error) {
	at, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		at, err = time.Parse("2006-01-02T15:04:05.999999999-07:00:00", value)
	}
	if err != nil || at.Year() < 1 || at.Year() > 9999 || at.Nanosecond()%1000 != 0 {
		return time.Time{}, invalid
	}
	return at, nil
}
func ordinal(at time.Time) int {
	return int(time.Date(at.Year(), at.Month(), at.Day(), 0, 0, 0, 0, time.UTC).Unix()/86400) + 719163
}
