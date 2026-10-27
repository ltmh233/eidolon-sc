// SPDX-License-Identifier: AGPL-3.0-or-later
package affinity

import (
	"testing"
	"time"
)

func TestRecoveryAndPublicState(t *testing.T) {
	now, _ := time.Parse(time.RFC3339Nano, "2026-10-27T12:00:00.123456+00:00")
	state := &Stored{Value: 0, UpdatedAtUS: now.Add(-12 * time.Hour).UnixMicro(), Cooling: true}
	got, err := Compute(Snapshot{Enabled: true, ObservedAt: now.Format(time.RFC3339Nano), State: state})
	if err != nil || got.Tier != "cold" || got.CooldownUntil == nil || *got.CooldownUntil != "2026-10-28T00:00:00.123456+00:00" {
		t.Fatalf("recover half a day: got %+v error %v", got, err)
	}
	for _, c := range []struct {
		value float64
		tier  string
	}{
		{19.999, "cold"}, {20, "distant"}, {39.999, "distant"}, {40, "trust"},
		{64.999, "trust"}, {65, "close"}, {84.999, "close"}, {85, "intimate"},
	} {
		got, err := Compute(Snapshot{Enabled: true, ObservedAt: now.Format(time.RFC3339Nano), State: &Stored{Value: c.value, UpdatedAtUS: now.UnixMicro()}})
		if err != nil || got.Tier != c.tier || got.CooldownUntil != nil {
			t.Fatalf("threshold %v: %+v %v", c, got, err)
		}
	}
}

func TestInitialDisabledFutureAndTimePrecision(t *testing.T) {
	observed := "2026-10-27T12:00:00+09:00"
	now, _ := time.Parse(time.RFC3339Nano, observed)
	initial, err := Compute(Snapshot{Enabled: true, ObservedAt: observed})
	if err != nil || initial.Tier != "trust" || initial.CooldownUntil != nil {
		t.Fatalf("missing state: %+v %v", initial, err)
	}
	disabled, err := Compute(Snapshot{Enabled: false})
	if err != nil || disabled != nil {
		t.Fatalf("disabled: %+v %v", disabled, err)
	}
	future, err := Compute(Snapshot{Enabled: true, ObservedAt: observed, State: &Stored{Value: 0, UpdatedAtUS: now.Add(24 * time.Hour).UnixMicro(), Cooling: true}})
	if err != nil || future.CooldownUntil == nil || *future.CooldownUntil != "2026-10-28T12:00:00+09:00" {
		t.Fatalf("future stored time: %+v %v", future, err)
	}
	for _, c := range []struct {
		value float64
		tier  string
	}{{0, "trust"}, {85, "intimate"}} {
		got, err := Compute(Snapshot{Enabled: true, ObservedAt: observed, State: &Stored{Value: c.value, UpdatedAtUS: now.Add(-100 * 24 * time.Hour).UnixMicro(), Cooling: true}})
		if err != nil || got.Tier != c.tier || got.CooldownUntil != nil {
			t.Fatalf("long idle %+v: %+v %v", c, got, err)
		}
	}
	fractional := Snapshot{Enabled: true, ObservedAt: "2026-10-27T12:00:00.100000+00:00", State: &Stored{Value: 0, UpdatedAtUS: time.Date(2026, 10, 27, 12, 0, 0, 100000000, time.UTC).UnixMicro(), Cooling: true}}
	got, err := Compute(fractional)
	if err != nil || got.CooldownUntil == nil || *got.CooldownUntil != "2026-10-28T12:00:00.100000+00:00" {
		t.Fatalf("fixed microseconds: %+v %v", got, err)
	}
}
