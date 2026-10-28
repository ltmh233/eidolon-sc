// SPDX-License-Identifier: AGPL-3.0-or-later
package timeline

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"io"
	"os"
	"sort"
)

const spoolChunk = 64 << 10

type cipherChunk struct {
	plain, disk int64
	size        int
}

type encryptedFile struct {
	disk                    *os.File
	aead                    cipher.AEAD
	chunks                  []cipherChunk
	writeErr                error
	pos, length, diskLength int64
}

// A fresh key exists only in request memory. Only authenticated ciphertext reaches
// disk, so unlinked blocks/snapshots do not retain a usable copy of the conversation.
func temporary() (*encryptedFile, error) {
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	disk, err := os.CreateTemp("", "eidolon-timeline-*")
	if err != nil {
		return nil, err
	}
	if err = os.Remove(disk.Name()); err != nil {
		disk.Close()
		return nil, err
	}
	return &encryptedFile{disk: disk, aead: aead}, nil
}

func nonce(index int) []byte {
	value := make([]byte, 12)
	binary.BigEndian.PutUint64(value[4:], uint64(index))
	return value
}
func associated(offset int64) []byte {
	value := make([]byte, 8)
	binary.BigEndian.PutUint64(value, uint64(offset))
	return value
}

func (f *encryptedFile) Write(data []byte) (int, error) {
	if f.writeErr != nil {
		return 0, f.writeErr
	}
	if f.pos != f.length {
		return 0, errors.New("spool is append-only")
	}
	written := 0
	for len(data) > 0 {
		size := min(len(data), spoolChunk)
		sealed := f.aead.Seal(nil, nonce(len(f.chunks)), data[:size], associated(f.length))
		n, err := f.disk.WriteAt(sealed, f.diskLength)
		if err != nil {
			f.writeErr = err
			return written, err
		}
		if n != len(sealed) {
			f.writeErr = io.ErrShortWrite
			return written, io.ErrShortWrite
		}
		f.chunks = append(f.chunks, cipherChunk{f.length, f.diskLength, size})
		f.length += int64(size)
		f.pos = f.length
		f.diskLength += int64(n)
		written += size
		data = data[size:]
	}
	return written, nil
}

func (f *encryptedFile) ReadAt(data []byte, offset int64) (int, error) {
	if offset < 0 {
		return 0, errors.New("negative spool offset")
	}
	copied := 0
	for len(data) > 0 {
		if offset >= f.length {
			return copied, io.EOF
		}
		index := sort.Search(len(f.chunks), func(i int) bool { return f.chunks[i].plain+int64(f.chunks[i].size) > offset })
		chunk := f.chunks[index]
		sealed := make([]byte, chunk.size+f.aead.Overhead())
		if _, err := f.disk.ReadAt(sealed, chunk.disk); err != nil {
			return copied, err
		}
		plain, err := f.aead.Open(nil, nonce(index), sealed, associated(chunk.plain))
		if err != nil {
			return copied, err
		}
		n := copy(data, plain[offset-chunk.plain:])
		copied += n
		offset += int64(n)
		data = data[n:]
	}
	return copied, nil
}
func (f *encryptedFile) Read(data []byte) (int, error) {
	n, err := f.ReadAt(data, f.pos)
	f.pos += int64(n)
	return n, err
}
func (f *encryptedFile) Seek(offset int64, whence int) (int64, error) {
	switch whence {
	case io.SeekCurrent:
		offset += f.pos
	case io.SeekEnd:
		offset += f.length
	case io.SeekStart:
	default:
		return f.pos, errors.New("invalid spool seek")
	}
	if offset < 0 {
		return f.pos, errors.New("negative spool seek")
	}
	f.pos = offset
	return offset, nil
}
func (f *encryptedFile) Close() error {
	err := f.disk.Close()
	f.aead = nil
	f.chunks = nil
	return err
}

// Fd allows the security regression to inspect actual unlinked ciphertext, not just names.
func (f *encryptedFile) Fd() uintptr { return f.disk.Fd() }
