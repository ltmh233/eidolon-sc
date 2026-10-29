// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"strings"
	"unicode/utf8"
)

const displayHeader = "X-Core-Display-Text"
const displayContract = "raw-fields-v1"

// visibleText matches one complete Python CueFilter.feed(field) + finish().
// Each field has independent state; network chunks and segments are never joined.
func visibleText(text string) string {
	var result strings.Builder
	space := ""
	take := func(part string) {
		if result.Len() == 0 {
			part = strings.TrimLeftFunc(part, pythonSpace)
		}
		body := strings.TrimRightFunc(part, pythonSpace)
		if body != "" {
			result.WriteString(space)
			result.WriteString(body)
			space = part[len(body):]
		} else {
			space += part
		}
	}
	for text != "" {
		opener := strings.IndexAny(text, "[(（【<")
		if opener < 0 {
			take(text)
			break
		}
		if opener > 0 {
			take(text[:opener])
			text = text[opener:]
			continue
		}
		first, size := utf8.DecodeRuneInString(text)
		closers := map[rune]string{'[': "]", '(': ")）", '（': ")）", '【': "】", '<': ">"}[first]
		close := strings.IndexAny(text[size:], closers)
		if close >= 0 {
			_, endSize := utf8.DecodeRuneInString(text[size+close:])
			text = text[size+close+endSize:]
			continue
		}
		// Only the >80 decision is needed; avoid a whole-field rune allocation/count.
		count := 0
		for range text {
			count++
			if count > 80 {
				break
			}
		}
		if count <= 80 {
			take(text)
			break
		}
		take(text[:size])
		text = text[size:]
	}
	return result.String()
}

// Python 3.11 str whitespace, including the four information separators.
func pythonSpace(r rune) bool {
	return r >= 0x9 && r <= 0xd || r >= 0x1c && r <= 0x20 || r == 0x85 || r == 0xa0 || r == 0x1680 || r >= 0x2000 && r <= 0x200a || r == 0x2028 || r == 0x2029 || r == 0x202f || r == 0x205f || r == 0x3000
}

// Go's JSON decoder replaces invalid UTF-8 and lone UTF-16 surrogates. The raw
// private contract rejects them before decoding, while accepting escaped slashes.
func validRawUnicode(data []byte) bool {
	if !utf8.Valid(data) {
		return false
	}
	hex := func(b byte) int {
		if b >= '0' && b <= '9' {
			return int(b - '0')
		}
		if b >= 'a' && b <= 'f' {
			return int(b - 'a' + 10)
		}
		if b >= 'A' && b <= 'F' {
			return int(b - 'A' + 10)
		}
		return -1
	}
	unit := func(at int) (int, bool) {
		if at+4 > len(data) {
			return 0, false
		}
		value := 0
		for _, b := range data[at : at+4] {
			digit := hex(b)
			if digit < 0 {
				return 0, false
			}
			value = value*16 + digit
		}
		return value, true
	}
	for i := 0; i < len(data); i++ {
		if data[i] != '"' {
			continue
		}
		i++
		for ; i < len(data) && data[i] != '"'; i++ {
			if data[i] != '\\' {
				continue
			}
			i++
			if i >= len(data) {
				return false
			}
			if data[i] != 'u' {
				continue
			}
			value, ok := unit(i + 1)
			if !ok {
				return false
			}
			i += 4
			if value >= 0xdc00 && value <= 0xdfff {
				return false
			}
			if value >= 0xd800 && value <= 0xdbff {
				if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
					return false
				}
				low, ok := unit(i + 3)
				if !ok || low < 0xdc00 || low > 0xdfff {
					return false
				}
				i += 6
			}
		}
	}
	return true
}
