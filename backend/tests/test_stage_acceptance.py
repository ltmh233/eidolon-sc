"""The capacity input is a reproducible artifact, independent of runtime responses."""
from tests.stage_acceptance import achievement_rows, history_text, manifest
import pytest


def test_fixed_stage_input_contract():
    rows = list(achievement_rows(100))
    assert len(rows) == len({(a, c) for a, c, _ in rows}) == 100
    assert rows[0] == ('turns', 'retired_000000', 0)
    assert rows[1] == ('tier', 'retired_000001', 1)
    assert rows[-1] == ('tier', 'retired_000099', 0)
    assert len(history_text(16)) == 16
    assert len(history_text(16).encode()) == 48
    result = manifest()
    assert [x['rows'] for x in result['achievement']] == [100, 10000, 100000]
    assert [x['canonical_jsonl_bytes'] for x in result['achievement']] == [2850, 285000, 2850000]
    assert [x['private_count_records_utf8_bytes'] for x in result['achievement']] == [5650, 565000, 5650000]
    assert [(x['turns'], x['tier']) for x in result['achievement']] == [(50, 50), (5000, 5000), (50000, 50000)]
    assert [x['value_counts'] for x in result['achievement']] == [[34, 33, 33], [3334, 3333, 3333], [33334, 33333, 33333]]
    assert [x['text_utf8_bytes_total'] for x in result['history']] == [1920000, 3145728]


def test_oom_evidence_blocks_even_successful_functional_and_next_batch(tmp_path):
    from tests.stage_acceptance import SafetyStop, assert_admitted, assert_no_oom
    (tmp_path / 'functional.exit').write_text('0\n')
    assert_admitted(tmp_path)
    (tmp_path / 'oom-stop').write_text('actual container child OOM event\n')
    with pytest.raises(SafetyStop, match='OOM'):
        assert_admitted(tmp_path)
    with pytest.raises(SafetyStop, match='OOM'):
        assert_no_oom(tmp_path)


async def test_capacity_entry_refuses_oom_before_reading_endpoints_or_sending_HTTP(tmp_path):
    from tests.stage_acceptance import SafetyStop
    from tests.stage_capacity import run
    (tmp_path / 'functional.exit').write_text('0\n')
    (tmp_path / 'oom-stop').touch()
    with pytest.raises(SafetyStop, match='OOM'):
        await run(tmp_path)


def test_operator_interrupt_is_a_safety_stop_without_claiming_OOM(tmp_path):
    from tests.stage_acceptance import SafetyStop, assert_admitted
    (tmp_path / 'functional.exit').write_text('0\n')
    (tmp_path / 'operator-interrupt').write_text('TERM\n')
    with pytest.raises(SafetyStop, match='operator interrupted'):
        assert_admitted(tmp_path)
    assert not (tmp_path / 'oom-stop').exists()
