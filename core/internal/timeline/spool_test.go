// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"runtime"
	"testing"
)

func TestRequestSpoolDoesNotPersistPlaintext(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("raw unlinked-fd inspection is verified in the Linux acceptance container")
	}
	file, err := temporary()
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	marker := bytes.Repeat([]byte("PRIVATE_CHAT_MUST_NOT_REMAIN_ON_DISK_"), 6000)
	if _, err = file.Write(marker); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(fmt.Sprintf("/proc/self/fd/%d", file.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(raw, []byte("PRIVATE_CHAT_MUST_NOT_REMAIN_ON_DISK_")) {
		t.Fatal("spool persisted plaintext")
	}
	for _, offset := range []int64{0, 1, 65535, 65536, int64(len(marker) - 17)} {
		got := make([]byte, min(97, len(marker)-int(offset)))
		if _, err = file.ReadAt(got, offset); err != nil {
			t.Fatalf("offset=%d: %v", offset, err)
		}
		if !bytes.Equal(got, marker[offset:offset+int64(len(got))]) {
			t.Fatalf("offset=%d: seek changed plaintext", offset)
		}
	}
	if _, err = file.Seek(0, io.SeekStart); err != nil {
		t.Fatal(err)
	}
	got, err := io.ReadAll(file)
	if err != nil || !bytes.Equal(got, marker) {
		t.Fatal("sequential round trip failed", err)
	}
}

func TestSpoolRejectsCorruptedCiphertext(t *testing.T) {
	file, err := temporary()
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if _, err = file.Write([]byte("PRIVATE_CHAT")); err != nil {
		t.Fatal(err)
	}
	damaged := []byte{0}
	if _, err = file.disk.ReadAt(damaged, 0); err != nil {
		t.Fatal(err)
	}
	damaged[0] ^= 1
	if _, err = file.disk.WriteAt(damaged, 0); err != nil {
		t.Fatal(err)
	}
	if _, err = file.ReadAt(make([]byte, 3), 0); err == nil {
		t.Fatal("unauthenticated ciphertext accepted")
	}
}

func TestSpoolWriteFailureCannotBeRetriedWithTheSameNonce(t *testing.T) {
	file, err := temporary()
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err = file.disk.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = file.Write([]byte("first")); err == nil {
		t.Fatal("closed backing file accepted write")
	}
	replacement, err := os.CreateTemp("", "eidolon-cipher-recovery-*")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(replacement.Name()); err != nil {
		replacement.Close()
		t.Fatal(err)
	}
	file.disk = replacement
	if _, err = file.Write([]byte("second")); err == nil {
		t.Fatal("failed chunk nonce could be reused after backing storage recovers")
	}
}
