// SPDX-License-Identifier: AGPL-3.0-or-later
// Package affinity owns the SC affinity read model. It never accesses SQLite.
package affinity

import (
	"errors"
	"fmt"
	"math"
	"time"
)

const (
	normalValue    = 50.0
	thawValue      = 10.0
	recoveryPerDay = 10.0
)

type Stored struct {
	Value       float64 `json:"value"`
	UpdatedAtUS int64   `json:"updated_at_us"`
	Cooling     bool    `json:"cooling"`
}

type Snapshot struct {
	Version     int     `json:"version"`
	WorkID      string  `json:"work_id"`
	CharacterID string  `json:"character_id"`
	Enabled     bool    `json:"enabled"`
	ObservedAt  string  `json:"observed_at"`
	State       *Stored `json:"state"`
}

type Status struct {
	Tier          string  `json:"tier"`
	CooldownUntil *string `json:"cooldown_until"`
}

func Compute(snapshot Snapshot) (*Status, error) {
	if !snapshot.Enabled {
		return nil, nil
	}
	now, err := time.Parse(time.RFC3339Nano, snapshot.ObservedAt)
	if err != nil || now.Nanosecond()%1000 != 0 {
		return nil, errors.New("invalid affinity observation time")
	}
	value, cooling := normalValue, false
	if state := snapshot.State; state != nil {
		value, cooling = state.Value, state.Cooling
		if math.IsNaN(value) || math.IsInf(value, 0) || state.UpdatedAtUS < -62135596800000000 || state.UpdatedAtUS > 253402300799999999 {
			return nil, errors.New("invalid stored affinity")
		}
		if value < normalValue {
			elapsed := math.Max(float64(now.UnixMicro()-state.UpdatedAtUS)/1e6, 0) / 86400
			value = math.Min(normalValue, value+recoveryPerDay*elapsed)
		}
		cooling = cooling && value < thawValue
	}
	tier := "cold"
	for _, candidate := range []struct {
		start float64
		name  string
	}{{20, "distant"}, {40, "trust"}, {65, "close"}, {85, "intimate"}} {
		if value >= candidate.start {
			tier = candidate.name
		}
	}
	result := &Status{Tier: tier}
	if cooling {
		micros := math.RoundToEven((thawValue - value) / recoveryPerDay * 86400000000)
		if micros < 0 || micros > float64(math.MaxInt64/1000) {
			return nil, errors.New("invalid affinity cooldown")
		}
		until := now.Add(time.Duration(int64(micros)) * time.Microsecond)
		if until.Year() < 1 || until.Year() > 9999 {
			return nil, errors.New("invalid affinity cooldown")
		}
		formatted := until.Format("2006-01-02T15:04:05")
		if until.Nanosecond() != 0 {
			formatted += fmt.Sprintf(".%06d", until.Nanosecond()/1000)
		}
		formatted += until.Format("-07:00")
		result.CooldownUntil = &formatted
	}
	return result, nil
}
