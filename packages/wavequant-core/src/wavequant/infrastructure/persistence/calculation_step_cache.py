"""Bounded JSON chunks for the 24 observable steps, isolated by exact input and engine."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
from time import monotonic
from uuid import uuid4
from typing import cast

from wavequant.domain.market_structure.calculation_plan import CalculationPlan
from wavequant.domain.market_structure.calculation_steps import STEP_TITLES
from wavequant.domain.models.model import Bar
from .artifact_cache import ArtifactCache
from .calculation_chunk import EvidenceJsonCache, TrendChunk


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError(f'unsupported calculation artifact: {type(value).__name__}')


def _encode_json(value: object) -> bytes:
    return json.dumps(_json_value(value), sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


class CalculationStepCache:
    """Stage data is inspectable JSON, never an executable pickle or a future-prefix substitute.

    Chunks have a 512 KiB target (a single row can exceed it); the shared artifact
    cache enforces its 4 GiB cap. Only sealed stages are advertised as stored.
    A manifest is published after every stage, so later failures retain earlier work.
    """

    def __init__(self, root: Path | None, identity: object, publish: Callable[[dict[str, object]], None]) -> None:
        self.cache = (ArtifactCache(root, read_timeout_seconds=ArtifactCache.WRITE_TIMEOUT_SECONDS)
                      if root is not None else None)
        self.identity = identity
        self.publish = publish
        self.attempt = uuid4().hex
        self.bars: tuple[Bar, ...] | None = None
        self.inputs: dict[str, object] | None = None
        self.plan: CalculationPlan | None = None
        self.steps: list[dict[str, object]] = [dict(id=i, title=title, status='pending', cache='none', rows=0)
            for i, title in enumerate(STEP_TITLES, 1)]
        self.buffers: dict[int, list[bytes]] = {}
        self.sizes: dict[int, int] = {}
        self.chunks: dict[int, list[dict[str, object]]] = {}
        self.trend_chunks: dict[int, TrendChunk] = {}
        self.evidence_json = EvidenceJsonCache()
        self.cache_disabled = False
        self.started: dict[int, float] = {}
        self.last_publish = 0.0
        self._emit()

    def snapshot(self) -> dict[str, object]:
        now = monotonic()
        rows = [{key: value for key, value in row.items() if key != 'chunks'} for row in self.steps]
        note = '清单为缓存产物，不代表独立计算步骤；共享跨度不能相加，编码和写入为实际累计耗时。'
        if self.plan is not None:
            for row in rows:
                number = cast(int, row['id'])
                if number in (1, 3, 10, 17):
                    row['title'] = f"同日变化联动 · {row['title']}"
                elif 4 <= number <= 23:
                    row['title'] = f"趋势内部产物 · {row['title']}"
            note = ('折线变化 → 一级变化 → 二级变化 → 三级，同一日期立即传播；'
                '各级内部同步生成六态，不安排独立六态计算或读取步骤。'
                '各级共用同一份来源数据，直接下级无变化则跳过上级趋势重算。'
                '达到对应级别且空翻多后，资格内部仅按需计算N／轧空确认；'
                '交易N、A波及一饱二吐五顶十满只在资格有效期间推进，关闭后停止，重开不补算空档。'
                '资格结束当天仅平仓并撤销未成交委托，区间外不撮合或判断持仓止损。'
                '当前日线回测在同一天继续判定B/S并推进账户，完成后才发布图表和交易日进度；'
                '第24项记录同日交易产物，不在全历史趋势之后另行回放。'
                '清单保留24种缓存产物编号，不能按已完成项数推算处理进度。'
                '趋势条数为变化版本，六态条数为事件；共享跨度不能相加，编码和写入为实际累计耗时。')
        return dict(schema_version=1, steps=[dict(row, elapsed_seconds=round(now - self.started[cast(int, row['id'])], 1))
            if row['status'] == 'running' else row for row in rows],
            note=note)

    def set_plan(self, plan: CalculationPlan) -> None:
        """Publish execution order without changing stable step or artifact identities."""
        if self.started and self.plan != plan:
            raise ValueError('calculation order cannot change after a step has started')
        self.plan = plan
        self._emit()

    def _emit(self, *, force: bool = True) -> None:
        now = monotonic()
        if force or now - self.last_publish >= 1:
            self.last_publish = now
            self.publish(self.snapshot())

    def bind(self, bars: Sequence[Bar]) -> bool:
        if self.bars is not None:
            return len(bars) == len(self.bars) and tuple(bars) == self.bars
        if not bars:
            return False
        self.bars = tuple(bars)
        digest = sha256()
        for bar in bars:
            digest.update(json.dumps(_json_value(bar), sort_keys=True, allow_nan=False).encode())
            digest.update(b'\n')
        self.inputs = dict(schema=4, request=self.identity, market_sha256=digest.hexdigest())
        if self.cache is not None:
            prior = self.cache.get('calculation-manifest', self.inputs)
            if isinstance(prior, dict) and isinstance(prior.get('steps'), list) and len(prior['steps']) == 24:
                for index, old in enumerate(prior['steps']):
                    chunks = old.get('chunks', []) if isinstance(old, dict) else []
                    if (index < 23 and isinstance(old, dict) and old.get('id') == index + 1 and old.get('status') == 'completed'
                            and old.get('cache') == 'stored' and isinstance(chunks, list)
                            and all(isinstance(key, dict) and self.cache.contains('calculation-chunk', key) for key in chunks)):
                        self.steps[index] = dict(old, status='pending', cache='available')
                        self.steps[index].pop('elapsed_seconds', None)
                        for field in ('encoding_seconds', 'write_seconds', 'encoded_bytes', 'reused_bytes'):
                            self.steps[index].pop(field, None)
                        self.chunks[index + 1] = chunks
        self._emit()
        return True

    def begin(self, step: int) -> None:
        row = self.steps[step - 1]
        if row['status'] != 'pending':
            return
        row.update(status='running')
        if row['cache'] != 'available':
            row['cache'] = 'partial' if self.cache_disabled else 'buffering'
            if self.cache_disabled:
                row['cache_error'] = '缓存写入中断，本轮已停止缓存；分析继续'
        row.update(encoding_seconds=0.0, write_seconds=0.0, encoded_bytes=0, reused_bytes=0)
        self.started[step] = monotonic()
        self._emit()

    def append(self, step: int, row: Mapping[str, object]) -> None:
        if self.steps[step - 1]['status'] == 'completed':
            return
        self.begin(step)
        if self.steps[step - 1]['cache'] == 'available':
            return
        if self.cache_disabled:
            self.steps[step - 1]['rows'] = cast(int, self.steps[step - 1]['rows']) + 1
            self._emit(force=False)
            return
        started = monotonic()
        reused = self.evidence_json.reused_bytes
        try:
            if step in (3, 10, 17):
                chunk = self.trend_chunks.setdefault(step, TrendChunk())
                encoded, size = chunk.encode(row, self.evidence_json, _encode_json)
            else:
                encoded = _encode_json(row)
                size = len(encoded)
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            self.steps[step - 1].update(cache='failed', cache_error=str(exc))
            return
        finally:
            self._add_metric(step, 'encoding_seconds', monotonic() - started)
            self._add_metric(step, 'reused_bytes', self.evidence_json.reused_bytes - reused)
        self.buffers.setdefault(step, []).append(encoded)
        self.sizes[step] = self.sizes.get(step, 0) + size
        self._add_metric(step, 'encoded_bytes', size)
        self.steps[step - 1]['rows'] = cast(int, self.steps[step - 1]['rows']) + 1
        if self.sizes[step] >= 512 * 1024 or step in self.trend_chunks and self.trend_chunks[step].full:
            self._flush(step)
        self._emit(force=False)

    def _add_metric(self, step: int, key: str, value: float | int) -> None:
        row = self.steps[step - 1]
        row[key] = cast(float | int, row.get(key, 0)) + value

    def _stop_cache(self) -> None:
        """缓存可丢弃；一次写失败后让本轮计算继续，避免每个 chunk 再等写锁。"""
        self.cache_disabled = True
        self.buffers.clear()
        self.sizes.clear()
        self.trend_chunks.clear()
        self.evidence_json.clear()
        for row in self.steps:
            if row['status'] == 'running' and row['cache'] != 'available':
                row.update(cache='partial', cache_error='缓存写入中断，本轮已停止缓存；分析继续')

    def _flush(self, step: int) -> None:
        rows = self.buffers.pop(step, [])
        self.sizes.pop(step, None)
        chunk = self.trend_chunks.pop(step, None)
        if not rows:
            return
        chunks = self.chunks.setdefault(step, [])
        key: dict[str, object] = dict(input=self.inputs, attempt=self.attempt, step=step, chunk=len(chunks))
        started = monotonic()
        try:
            payload = chunk.finish(rows) if chunk is not None else b'[' + b','.join(rows) + b']'
            if self.cache is not None and self.cache._put_json('calculation-chunk', key, payload):
                chunks.append(key)
            else:
                self._stop_cache()
        finally:
            self._add_metric(step, 'write_seconds', monotonic() - started)

    def complete(self, step: int) -> None:
        row = self.steps[step - 1]
        if row['status'] == 'completed':
            return
        self.begin(step)
        self._flush(step)
        row.update(status='completed', elapsed_seconds=round(monotonic() - self.started[step], 1),
            chunks=self.chunks.get(step, []))
        if row['cache'] not in ('failed', 'available', 'partial'):
            row['cache'] = 'stored' if self.cache is not None else 'failed'
        self._save()
        self._emit()

    def _save(self) -> bool:
        if self.cache_disabled:
            return False
        if self.cache is not None and self.inputs is not None:
            rows = [dict(row, cache='stored') if row['cache'] == 'available' else dict(row) for row in self.steps]
            manifest: dict[str, object] = dict(steps=rows)
            if self.plan is not None:
                manifest.update(requested_order=list(self.plan.requested_order), execution_order=list(self.plan.execution_order))
            if self.cache.put('calculation-manifest', self.inputs, manifest):
                return True
            logging.warning('Calculation step manifest was not persisted')
            self._stop_cache()
            for row in self.steps:
                if row['cache'] == 'stored':
                    row.update(cache='partial', cache_error='缓存清单写入中断，本轮已停止缓存；分析继续')
        return False

    def finish(self, result: object, error: Exception | None = None) -> None:
        if error is None and isinstance(result, dict) and self.inputs is not None:
            self.append(24, {key: result[key] for key in ('signals', 'audit', 'orders', 'trades', 'curve', 'metrics', 'backtest')
                if key in result})
            self.complete(24)
            backtest = result.get('backtest')
            if isinstance(backtest, dict) and backtest.get('status') == 'data_unavailable':
                self.steps[23].update(status='failed', error='同源分钟数据不足，交易回测未完成')
        for row in self.steps:
            if row['status'] == 'running':
                self._flush(cast(int, row['id']))
                row.update(status='failed', error=str(error) if error else '步骤未完成',
                    elapsed_seconds=round(monotonic() - self.started[cast(int, row['id'])], 1),
                    chunks=self.chunks.get(cast(int, row['id']), []))
                if row['cache'] not in ('failed', 'available', 'partial'):
                    row['cache'] = 'partial' if self.chunks.get(cast(int, row['id'])) else 'none'
            elif row['status'] == 'pending':
                if error is None and row['cache'] == 'available':
                    row.update(status='completed')
                else:
                    row.update(status='not_recorded', error='前序计算失败，未执行此步骤' if error
                        else '当前策略或旧缓存未提供此步骤数据')
        self._save()
        self.evidence_json.clear()
        self._emit()
