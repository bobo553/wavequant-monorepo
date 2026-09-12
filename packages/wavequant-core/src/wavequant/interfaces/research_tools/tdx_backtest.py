"""On-demand independent daily research on one mutable TDX security.

Raw browsing is separate. A run owns its adjusted bars, ledger and rule evidence;
there is no raw-price fallback when corporate-action conversion fails.
"""
from dataclasses import asdict
from collections import OrderedDict
from datetime import date, datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from time import perf_counter

from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, SystemResult, generate_system_signals
from wavequant.domain.models.model import Bar, Signal
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result
from wavequant.infrastructure.market_data.tdx import read_day, read_actions, adjust_rows
from wavequant.application.analytics.trade_evidence import result_markers


class TdxBacktester:
    def __init__(self,browser,cache):
        self.browser=browser; self.cache=Path(cache)
        self.memory=OrderedDict();self.memory_lock=Lock()
        self.artifacts=ArtifactCache(self.cache)
        self.actions_lock=Lock(); self.actions_hash=None; self.actions_by_symbol={}
        self.engine=self._engine_hashes()

    @staticmethod
    def _engine_hashes():
        # Raw chart theory depends on domain reducers as well as this adapter.
        # Hash the complete package with relative keys so a reducer change cannot
        # silently reuse an artifact produced by older trend rules.
        package_root=Path(__file__).parents[2]
        paths=sorted(package_root.rglob('*.py'))
        versions=tuple((str(path),stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size)
                       for path in paths for stat in [path.stat()])
        return dict(_hashed_engine(str(package_root),versions))

    def _actions(self,path):
        digest=fingerprint(path)
        with self.actions_lock:
            if digest!=self.actions_hash:
                actions,decoded_hash=read_actions(path,self.cache)
                if digest!=decoded_hash or fingerprint(path)!=digest:
                    raise ValueError('通达信除权文件正在更新，请更新结束后重新运行')
                indexed={}
                for event in actions:
                    indexed.setdefault(event['symbol'],[]).append(json.dumps(event,sort_keys=True))
                self.actions_by_symbol={s:tuple(events) for s,events in indexed.items()}
                self.actions_hash=digest
            return self.actions_by_symbol,digest

    def _verify(self,inputs):
        if self._engine_hashes()!=inputs['engine']:
            raise ValueError('运行中策略代码已变更，请重启服务后重试')
        if (fingerprint(self.browser._path(inputs['symbol']))!=inputs['day_sha256'] or
                fingerprint(self.browser.root/'T0002/hq_cache/gbbq')!=inputs['gbbq_sha256']):
            with self.memory_lock:self.memory.clear()
            raise ValueError('通达信文件正在更新，请更新结束后重新运行，未返回混合版本结果')

    def screen(self,symbol,start,asof,strategy,execution,lookback):
        if type(lookback) is not int or lookback not in (1,5,20):
            raise ValueError('lookback must be 1, 5 or 20')
        return self.run(symbol,start,asof,strategy,execution,_screening=lookback)

    def run(self,symbol,start,asof,strategy,execution,*,_screening=None):
        started=perf_counter()
        first,last=date.fromisoformat(start),date.fromisoformat(asof)
        if first.isoformat()!=start or last.isoformat()!=asof or first>last:
            raise ValueError('回测起止日期无效，请使用 YYYY-MM-DD')
        if not re.fullmatch(r'(sh\.60\d{4}|sz\.00\d{4})',symbol):
            raise ValueError('当前回测执行模型仅支持沪深主板非 ST 股票；创业板、科创板、北交所仍可浏览，不能套用主板规则回测')
        metadata=self.browser.stock_metadata(symbol)
        if metadata and any(tag in (metadata.get('name') or '').upper() for tag in ('ST','退')):
            raise ValueError('当前为风险警示或退市名称，缺少历史状态记录，暂不生成回测成交')
        path=self.browser._path(symbol);actions_path=self.browser.root/'T0002/hq_cache/gbbq'
        if not actions_path.is_file(): raise ValueError('缺少通达信 gbbq 除权数据，不能把未复权行情当作回测行情')
        engine=self._engine_hashes()
        if engine!=self.engine: raise ValueError('策略代码已变更，请重启图表服务后再运行，避免新版本标识对应旧引擎')
        day_hash=fingerprint(path);actions,action_hash=self._actions(actions_path)
        events=actions.get(symbol,())
        # Single-flight per symbol, not a global lock blocking chart inspection
        # behind a different stock's cold calculation.
        with self.artifacts.lock(self.artifacts.key('stock',symbol)):
            key=json.dumps(dict(symbol=symbol,start=start,asof=asof,strategy=strategy,execution=execution,
                day_sha256=day_hash,gbbq_sha256=action_hash,engine=engine,
                decoded_actions_sha256=hashlib.sha256(json.dumps(events).encode()).hexdigest()),sort_keys=True)
            inputs=json.loads(key)
            summary=self.artifacts.get('screen',inputs)
            if _screening is not None and summary is not None:
                self._verify(inputs)
                return dict(summary[str(_screening)],source=summary['source'],
                            performance=dict(cache='screen_disk',elapsed_seconds=perf_counter()-started))
            with self.memory_lock:
                bundle=self.memory.get(key)
                if bundle is not None:self.memory.move_to_end(key)
            if bundle is None:
                bars,generated,view,status=self._run(key,events)
            else:
                bars,generated,view=bundle;status='memory'
            self._verify(inputs)
            with self.memory_lock:
                self.memory[key]=(bars,generated,view)
                self.memory.move_to_end(key)
                while len(self.memory)>8:self.memory.popitem(last=False)
            if summary is None:
                from wavequant.interfaces.screening.buy_scanner import buy_match
                from wavequant.application.analytics.screening_funnel import funnel
                summary={'source':view['backtest']['source']}
                for window in (1,5,20):
                    match,skip=buy_match(view,asof,window)
                    summary[str(window)]=dict(match=match,skip=skip,gates=funnel(view,window))
                self.artifacts.put('screen',inputs,summary)
            performance=dict(cache=status,elapsed_seconds=perf_counter()-started)
            if _screening is not None:
                return dict(summary[str(_screening)],source=summary['source'],performance=performance)
            return bars,generated,dict(view,performance=performance)

    def _run(self,key,events):
        inputs=json.loads(key)
        cached=self.artifacts.get('backtest',inputs)
        if cached is not None:
            bars,generated=decode_research(cached['research'])
            return bars,generated,cached['view'],'disk'
        signal_inputs={k:v for k,v in inputs.items() if k!='execution'}
        research=self.artifacts.get('signals',signal_inputs)
        signal_hit=research is not None
        if research is None:
            bars,generated,all_raw,start=self._generate(inputs,events)
            research=encode_research(bars,generated)
            research['sessions']=[r['date'].isoformat() for r in all_raw if r['date']>=start]
            self._verify(inputs)  # Never publish an artifact from a moving source.
            self.artifacts.put('signals',signal_inputs,research)
        else:
            bars,generated=decode_research(research)
        strategy=SystemStrategy(**inputs['strategy']);strategy.validate()
        result=single_stock_result(bars,asdict(strategy),inputs['execution'],generated)
        view=self._view(key,inputs,bars,result,research['sessions'])
        self._verify(inputs)
        self.artifacts.put('backtest',inputs,dict(research=research,view=view))
        return bars,generated,view,'signals_disk' if signal_hit else 'computed'

    def _generate(self,inputs,events):
        symbol=inputs['symbol'];end=date.fromisoformat(inputs['asof'])
        all_raw=read_day(self.browser._path(symbol))
        raw=[r for r in all_raw if r['date']<=end]
        if len(raw)<22: raise ValueError('历史日线不足：须先跳过至少 20 个已有交易日，避免上市初期规则误用')
        start=max(date.fromisoformat(inputs['start']),raw[20]['date'])
        if start>end: raise ValueError('所选区间没有可回测日线')
        converted=adjust_rows(raw,[json.loads(e) for e in events],start,end,symbol)
        if not converted: raise ValueError('所选区间没有可回测日线')
        bars=[Bar(datetime.fromisoformat(r['timestamp']),symbol,
                  *(r[k] for k in ('open','high','low','close','volume')),
                  bool(r['buyable']),bool(r['sellable']),r['adjustment_factor']) for r in converted]
        strategy=SystemStrategy(**inputs['strategy']);strategy.validate()
        generated=generate_system_signals(bars,strategy)
        # Persist chart geometry during the first scan as well as on chart open.
        # It is independent of strategy/execution, but never of price basis.
        from wavequant.interfaces.charts.chart_geometry import cached_geometry
        cached_geometry(self.artifacts,bars,'causal_adjusted_equivalent',inputs['engine'])
        return bars,generated,all_raw,start

    def _view(self,key,inputs,bars,result,sessions):
        symbol=inputs['symbol']
        from wavequant.interfaces.charts.visualization import metrics_at
        _,curve=metrics_at(result['equity'],result['trades'],result['backtest']['initial_capital'])
        run_id='tdx-'+hashlib.sha256(key.encode()).hexdigest()[:24]
        result['backtest'].update(provenance='current_engine_on_local_tdx_prefix',run_id=run_id,
            requested_start=inputs['start'],price_basis='causal_adjusted_equivalent',
            source=dict(day_sha256=inputs['day_sha256'],gbbq_sha256=inputs['gbbq_sha256'],engine=inputs['engine'],
                        decoded_actions_sha256=inputs['decoded_actions_sha256']),
            adjustment='固定起点、逐日累乘除权因子；成交等价价 ÷ 当日因子 = 模拟原始成交价',
            limitations=['仅沪深主板；跳过本地最早 20 个交易日，不模拟新股上市规则。',
                '日线无历史 ST / 退市状态，按历史非 ST 主板研究假设运行，非完整可交易性证明。',
                '除权采用等价份额近似，不是现金分红税、配股缴款的真实现金账本。',
                '日线收盘观察、下一根可交易开盘模拟成交；无集合竞价排队、逐笔成交或盘中止损保证。',
                '旧严格版遇包含线中断；新版母子顺序来自讲义约定，不等于真实盘中路径；代理版是独立对照。',
                '日线面板未提供次级周期转折，正负扭转的次级趋势线确认不参与当前入场或退出。'])
        view=dict(run_id=run_id,symbol=symbol,asof=inputs['asof'],result_scope='stock',data_source='tdx',
            price_basis='causal_adjusted_equivalent',
            sessions=sessions,
            bars=[dict(time=b.timestamp.date().isoformat(),open=b.open,high=b.high,low=b.low,close=b.close,
                factor=b.adjustment_factor,raw_close=b.close/b.adjustment_factor,volume=b.volume) for b in bars],
            markers=result_markers(result),signals=result['signals'],orders=result['orders'],audit=result['audit'],
            trades=result['trades'],metrics=result['metrics'],curve=curve,backtest=result['backtest'],
            evidence='通达信当前股票独立回测；'+('已有模拟成交，不代表策略有效' if result['metrics']['entry_fills'] else '未产生成交，请查看筛选证据'))
        return view


@lru_cache(maxsize=4)
def _hashed_engine(package_root,versions):
    root=Path(package_root)
    return tuple((Path(path).relative_to(root).as_posix(),fingerprint(Path(path)))
                 for path,*_ in versions)


def encode_research(bars,generated):
    return dict(bars=[dict(asdict(b),timestamp=b.timestamp.isoformat()) for b in bars],
        signals=[dict(asdict(s),timestamp=s.timestamp.isoformat(),trigger_timestamp=s.trigger_timestamp.isoformat())
                 for s in generated.signals],audit=generated.audit,counts=generated.counts)


def decode_research(value):
    bars=[Bar(**dict(b,timestamp=datetime.fromisoformat(b['timestamp']))) for b in value['bars']]
    signals=[Signal(**dict(s,timestamp=datetime.fromisoformat(s['timestamp']),
                          trigger_timestamp=datetime.fromisoformat(s['trigger_timestamp']))) for s in value['signals']]
    return bars,SystemResult(signals,value['audit'],value['counts'])
