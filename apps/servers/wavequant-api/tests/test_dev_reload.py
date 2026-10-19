from pathlib import Path

from wavequant_api.dev import source_snapshot
from wavequant_api.server import backtest_job_error


def test_source_snapshot_tracks_python_additions_changes_and_deletions(tmp_path: Path):
    roots = (tmp_path,)
    before = source_snapshot(roots)
    source = tmp_path / 'rule.py'
    source.write_text('value = 1', encoding='utf-8')
    added = source_snapshot(roots)
    assert added != before
    source.write_text('value = 12345', encoding='utf-8')
    assert source_snapshot(roots) != added
    source.unlink()
    assert source_snapshot(roots) == before
    (tmp_path / 'notes.md').write_text('not executable source', encoding='utf-8')
    assert source_snapshot(roots) == before


def test_changed_engine_is_a_transient_service_issue_not_invalid_stock_data():
    status, message = backtest_job_error(ValueError('运行中策略代码已变更，请重启服务后重试'))
    assert status == 503
    assert '策略服务正在更新' in message
    assert backtest_job_error(ValueError('invalid start date'))[0] == 400
