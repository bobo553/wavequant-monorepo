from datetime import datetime, timedelta

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult
from wavequant.interfaces.research_tools.stock_backtest import simulation_progress_callbacks, single_stock_result


def test_preparation_stages_are_observable_before_daily_progress_without_changing_account():
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), 'sz.000678', 10, 11, 9, 10, 100_000)
            for i in range(8)]
    execution = StrategyConfig(staged_exit_intraday=False, consolidation_entry_intraday=True,
                               trend_flip_adverse_exit=True).to_dict()
    generated = SystemResult([], [], {})
    expected = single_stock_result(bars, {}, execution, generated)
    updates: list[tuple[int, str]] = []
    progress, stage = simulation_progress_callbacks(lambda percent, name: updates.append((percent, name)))
    actual = single_stock_result(bars, {}, execution, generated, progress=progress, stage=stage)
    assert actual == expected
    stages = [name for _, name in updates]
    assert stages.index('准备分钟入场历史') < stages.index('核验分钟入场')
    assert stages.index('准备压力与量价退出风险') < stages.index('准备三级趋势退出历史')
    assert stages.index('准备三级趋势退出历史') < stages.index('逐日模拟撮合') < stages.index('模拟成交')
    assert stages[-1] == '整理成交证据'
    assert [percent for percent, _ in updates] == sorted(percent for percent, _ in updates)
    assert updates[0][0] == 65 and updates[-1][0] == 90


def test_late_stage_message_retains_latest_simulation_percentage():
    updates: list[tuple[int, str]] = []
    progress, stage = simulation_progress_callbacks(lambda percent, name: updates.append((percent, name)))
    assert progress is not None and stage is not None
    stage('准备退出历史')
    progress(60)
    stage('整理成交证据')
    assert updates == [(65, '准备退出历史'), (80, '模拟成交'), (80, '整理成交证据')]
