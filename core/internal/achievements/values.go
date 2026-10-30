// SPDX-License-Identifier: AGPL-3.0-or-later
package achievements

import (
	"math"
	"math/big"
)

// DecodeScalar and DecodeTime share the established Python SQL wire profile.
func DecodeScalar(v any, key bool) (Scalar, error)        { return scalar(v, key) }
func DecodeTime(v any) (Time, error)                      { return decodeTime(v) }
func RuleInteger(v any, digits int64) (*big.Int, error)   { return ruleInteger(v, digits) }
func Size(v any) (int64, error)                           { return size(v) }
func ValidJSON(v []byte) bool                             { return validJSONText(v) }
func Tier(rank *big.Int, names []string) (any, error)     { return tier(rank, names) }
func (s Scalar) Identity() string                         { return s.identity() }
func (s Scalar) PythonInt(digits int64) (*big.Int, error) { return s.pythonInt(digits) }
func (s Scalar) Kind() string                             { return s.kind }
func (s Scalar) Value() string                            { return s.value }
func (s Scalar) Float() float64                           { return s.float() }
func IntegerScalar(n *big.Int) Scalar                     { return Scalar{"i", n.String()} }

// CompareInteger preserves Python's exact mixed INTEGER/REAL comparisons.
// NaN is unordered; TEXT and BLOB cannot be compared to rule integers.
func (s Scalar) CompareInteger(n *big.Int) (order int, ordered bool, err error) {
	switch s.kind {
	case "i":
		v, _ := new(big.Int).SetString(s.value, 10)
		return v.Cmp(n), true, nil
	case "f":
		f := s.float()
		if math.IsNaN(f) {
			return 0, false, nil
		}
		if math.IsInf(f, 1) {
			return 1, true, nil
		}
		if math.IsInf(f, -1) {
			return -1, true, nil
		}
		return new(big.Rat).SetFloat64(f).Cmp(new(big.Rat).SetInt(n)), true, nil
	}
	return 0, false, ErrLegacy
}
