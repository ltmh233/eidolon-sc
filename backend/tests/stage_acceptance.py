"""Synthetic inputs and CLI for the opt-in local stage acceptance (never production)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets

SEED = 20261030
SIZES = {'S': 100, 'M': 10000, 'L': 100000}
HISTORY_SHAPES = (('many-small', 10000, 64), ('one-large', 1, 1048576))


class SafetyStop(RuntimeError):
    """A measured role OOM or lost observer forbids further load in this instance."""


def assert_no_oom(directory):
    if (directory / 'oom-stop').exists():
        raise SafetyStop('actual container OOM evidence: remaining batches and higher concurrency safety-stopped')
    if (directory / 'operator-interrupt').exists():
        raise SafetyStop('operator interrupted observer: remaining load safety-stopped')
    if (directory / 'oom-watch-failed').exists():
        raise SafetyStop('OOM event observer lost: remaining load safety-stopped')


def assert_admitted(directory):
    assert_no_oom(directory)
    if (directory / 'functional.exit').read_text().strip() != '0':
        raise SafetyStop('functional baseline did not pass')


def achievement_rows(count):
    for index in range(count):
        yield ('turns' if index % 2 == 0 else 'tier', f'retired_{index:06d}', index % 3)


def history_text(length):
    return ('合成文' * ((length + 2) // 3))[:length]


def manifest():
    from eidolon.gifts_panel import _encode
    from eidolon.achievement_snapshot import _scalar
    result = {'seed': SEED, 'achievement': [], 'history': []}
    for label, count in SIZES.items():
        rows = list(achievement_rows(count))
        encoded = b''.join(_encode(['count', _scalar(a), _scalar(c), _scalar(n)]) for a, c, n in rows)
        canonical = b''.join((json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n').encode() for row in rows)
        result['achievement'].append({
            'label': label, 'rows': count, 'distinct_retired_characters': count,
            'turns': count // 2, 'tier': count // 2,
            'value_counts': [sum(n == v for _, _, n in rows) for v in range(3)],
            'character_id_characters_and_utf8': 14,
            'canonical_jsonl_bytes': len(canonical), 'canonical_sha256': hashlib.sha256(canonical).hexdigest(),
            'private_count_records_utf8_bytes': len(encoded), 'private_count_records_sha256': hashlib.sha256(encoded).hexdigest(),
        })
    for shape, count, length in HISTORY_SHAPES:
        text = history_text(length)
        encoded = json.dumps(text, ensure_ascii=False, separators=(',', ':')).encode()
        result['history'].append({'shape': shape, 'rows': count, 'text_characters': length,
                                  'text_utf8_bytes_each': len(text.encode()),
                                  'json_string_utf8_bytes_each': len(encoded),
                                  'text_utf8_bytes_total': len(text.encode()) * count,
                                  'text_sha256_each': hashlib.sha256(text.encode()).hexdigest()})
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['manifest', 'release'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'manifest':
        args.output.write_text(json.dumps(manifest(), ensure_ascii=False, indent=2) + '\n')
    else:
        from tests.test_character_snapshot import CONFIG, export
        from eidolon.content_publish import publish
        args.output.mkdir()
        result = export(CONFIG, args.output / 'content.json', with_gifts=True)
        assert result.returncode == 0, result.stderr.decode()
        publish(args.output / 'content.json', CONFIG, None, args.output / 'display.json')
        for name in ('core-token', 'create-token', 'panel-token'):
            path = args.output / name
            path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        (args.output / 'release.json').write_text(json.dumps({
            'core_sha256': hashlib.sha256(Path(os.environ['EIDOLON_CORE_BINARY']).read_bytes()).hexdigest(),
            'content_sha256': hashlib.sha256((args.output / 'content.json').read_bytes()).hexdigest(),
            'display_sha256': hashlib.sha256((args.output / 'display.json').read_bytes()).hexdigest(),
        }, indent=2))


if __name__ == '__main__':
    main()
