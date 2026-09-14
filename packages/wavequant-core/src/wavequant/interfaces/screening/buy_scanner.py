"""Explicit, cancellable local screening jobs. No order submission or strategy relaxation."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from threading import Condition, Event, Lock, Thread
from uuid import uuid4
import re
from time import perf_counter

from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.application.analytics.screening_funnel import funnel


def buy_match(view,asof,lookback):
    """Newest fresh-entry LONG in the requested prefix window, not a trailing update.

    A historical match is a historical signal, not a still-valid instruction.
    The current-day view only returns signals awaiting the next opening test.
    """
    bars=view['bars']
    if not bars or bars[-1]['time']!=asof: return None,'stale'
    window={b['time'] for b in bars[-lookback:]}
    for signal in reversed(view['signals']):
        when=signal['timestamp'];day=when[:10]
        if signal['side']!='LONG' or day not in window: continue
        held=False
        for order in view['orders']:
            if order['timestamp']>when: break
            if order['status']=='filled': held=order['side']=='BUY'
        if held: continue  # Existing-account LONG raises a trailing stop, not a new entry.
        orders=[o for o in view['orders'] if o['side']=='BUY' and o.get('signal_timestamp')==when]
        fill=next((o for o in orders if o['status']=='filled'),None)
        reject=next((o for o in reversed(orders) if o['status']=='cancelled'),None)
        invalid=next((s for s in view['signals'] if s['side']=='EXIT' and s['timestamp']>when),None)
        status='filled' if fill else 'rejected' if reject else 'invalidated' if invalid else (
            'awaiting_next_open' if day==asof else 'historical_unfilled')
        if lookback==1 and status!='awaiting_next_open': continue
        bar=next(b for b in bars if b['time']==day)
        eligible_sessions=[b['time'] for b in bars if b['time']<=asof]
        risk=signal['reference_price']-signal['invalidation_price']
        target=signal.get('target_price')
        evidence=[e for e in view.get('audit',[]) if e['timestamp']==when and
                  e['event'] in ('long_signal','long_transition_evidence')]
        proof=next((e for e in evidence if e['event']=='long_transition_evidence'),{})
        return dict(symbol=view['symbol'],signal_date=day,status=status,regime=signal['regime'],
            reference_price=signal['reference_price'],raw_reference_price=signal['reference_price']/bar['factor'],
            stop=signal['invalidation_price'],target=target,rvol=signal['rvol'],retracement=signal['retracement'],
            gross_reward_risk=(target-signal['reference_price'])/risk if target is not None and risk>0 else None,
            reason=signal['reason'],execution_reason=(fill or reject or {}).get('reason'),
            fill_date=fill['timestamp'][:10] if fill else None,evidence=evidence,
            buy_point_type=proof.get('buy_point_type'),priority=proof.get('priority',0),trend_level=proof.get('trend_level'),
            price_basis=view['price_basis'],run_id=view.get('run_id'),
            source=view.get('backtest',{}).get('source'),data_source=view.get('data_source'),
            resolved_source=view.get('resolved_source'),providers=view.get('providers',[]),
            supplemented_bars=view.get('supplemented_bars',0),asof=asof,
            session_age=len(eligible_sessions)-eligible_sessions.index(day)),None
    return None,None


class BuyScanner:
    def __init__(self,repository):
        self.repository=repository;self.lock=Lock();self.jobs={};self.stops={}
        self.changed=Condition(self.lock)
        from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester
        self.engine_hashes=TdxBacktester._engine_hashes;self.engine=self.engine_hashes()

    def start(self,params):
        expected={'run','variant','scenario','source','asof','start','lookback'}
        if set(params) not in (expected,expected|{'symbol'}):
            raise ValueError('invalid screening arguments')
        from wavequant.interfaces.charts.visualization import VARIANTS,SCENARIOS
        if self.engine_hashes()!=self.engine: raise ValueError('策略代码已变更，请重启服务后再扫描')
        p=dict(params);repo=self.repository;repo._run(p['run'])
        if p['variant'] not in VARIANTS or p['scenario'] not in SCENARIOS or p['source'] not in ('tdx','snapshot','akshare'):
            raise ValueError('invalid screening source or strategy')
        if (p['source']=='akshare') != ('symbol' in p):
            raise ValueError('AkShare screening requires exactly one symbol')
        if type(p['lookback']) is not int or p['lookback'] not in (1,5,20): raise ValueError('lookback must be 1, 5 or 20')
        for key in ('start','asof'):
            if not isinstance(p[key],str) or date.fromisoformat(p[key]).isoformat()!=p[key]: raise ValueError('invalid date')
        if p['start']>p['asof']: raise ValueError('回测起点不能晚于回放日期')
        config=repo.strategy_config(p['run'],p['variant'])
        if config['strategy'].get('entry_policy','transitioned_squeeze') not in ('transitioned_squeeze','hierarchical_two_buy_points'):
            raise ValueError('买点筛选仅支持趋势交替后的轧空策略')
        skipped=Counter();stocks=[];versions={};action_hash=None
        if p['source']=='tdx':
            if repo.tdx is None: raise ValueError('通达信目录未配置')
            action_path=repo.tdx.root/'T0002/hq_cache/gbbq'
            if not action_path.is_file(): raise ValueError('缺少 gbbq，不能进行统一复权筛选')
            action_hash=fingerprint(action_path)
            catalog=repo.tdx.catalog()['stocks']
            for s in catalog:
                reason=('unsupported_board' if not re.fullmatch(r'(sh\.60\d{4}|sz\.00\d{4})',s['symbol']) else
                        'no_daily' if not s.get('has_data') else
                        'current_st_or_delisted' if any(t in (s.get('name') or '').upper() for t in ('ST','退')) else
                        'stale_daily' if s.get('last','')<p['asof'] else None)
                if reason: skipped[reason]+=1;continue
                stat=repo.tdx._path(s['symbol']).stat();versions[s['symbol']]=(stat.st_mtime_ns,stat.st_size)
                stocks.append(dict(symbol=s['symbol'],name=s.get('name')))
        elif p['source']=='snapshot':
            if p['asof']>repo.runs[p['run']]['report']['data']['end']: raise ValueError('回放日超出封存样本')
            stocks=[dict(symbol=s,name=None) for s in sorted(repo.bars(p['run']))]
        else:
            if repo.akshare is None: raise ValueError('AkShare 数据源未配置')
            stock=next((item for item in repo.market_data.catalog('akshare')['stocks'] if item['symbol']==p['symbol']),None)
            if stock is None: raise ValueError('AkShare 股票不在可用目录')
            stocks=[dict(symbol=p['symbol'],name=stock.get('name'))]
        with self.lock:
            for job in self.jobs.values():
                if job['status'] in ('running','cancelling'):
                    if job['params']==p and job['status']=='running': return deepcopy(job)
                    raise ValueError('已有扫描在运行，请先取消或等待完成')
            while len(self.jobs)>=4:
                old=next(iter(self.jobs));self.jobs.pop(old);self.stops.pop(old,None)
            ident=uuid4().hex;stop=Event()
            job=dict(id=ident,params=p,status='running',revision=0,total=len(stocks),processed=0,failed=0,stale=0,
                skipped=sum(skipped.values()),skip_reasons=dict(skipped),results=[],errors=[],current=None,
                created_at=datetime.now().astimezone().isoformat(),error=None,
                notice=('AkShare 只分析当前股票的原始不复权信号，不模拟成交；结果不代表当前可买。'
                    if p['source']=='akshare' else
                    '仅筛选当时策略信号，次开盘仍须通过执行风控；历史信号不代表当前可买。'))
            job['funnel']=dict(stocks={},events={},rejections={})
            job['performance']=dict(cache_hits=0,recomputed=0,elapsed_seconds=0)
            self.jobs[ident]=job;self.stops[ident]=stop
            Thread(target=self._work,args=(ident,stocks,config,versions,action_hash),daemon=True).start()
            return deepcopy(job)

    def _work(self,ident,stocks,config,versions,action_hash):
        repo=self.repository;p=self.jobs[ident]['params'];stop=self.stops[ident]
        started=perf_counter()
        try:
            for stock in stocks:
                if stop.is_set(): break
                if self.engine_hashes()!=self.engine: raise RuntimeError('扫描期间策略代码已变化，结果作废；请重启服务')
                symbol=stock['symbol']
                with self.lock:
                    self.jobs[ident]['current']=symbol
                    self._publish(ident)
                try:
                    if p['source']=='tdx':
                        stat=repo.tdx._path(symbol).stat()
                        if (stat.st_mtime_ns,stat.st_size)!=versions[symbol]: raise RuntimeError('扫描期间日线已更新，请重新扫描')
                        summary=repo.tdx_backtester.screen(symbol,p['start'],p['asof'],config['strategy'],config['scenarios'][p['scenario']]['execution'],p['lookback'])
                        if summary['source']['gbbq_sha256']!=action_hash: raise RuntimeError('扫描期间除权数据已更新，请重新扫描')
                        match,skip,gates=summary['match'],summary['skip'],summary['gates']
                        with self.lock:
                            field='recomputed' if summary['performance']['cache']=='computed' else 'cache_hits'
                            self.jobs[ident]['performance'][field]+=1
                    elif p['source']=='snapshot':
                        end=repo.bars(p['run'])[symbol][-1].timestamp.date().isoformat()
                        view=repo.stock_view(p['run'],p['variant'],symbol,min(end,p['asof']),p['scenario'])
                        match,skip=buy_match(view,p['asof'],p['lookback'])
                        gates=funnel(view,p['lookback'])
                    else:
                        view=repo.akshare_signal_view(
                            p['run'],p['variant'],symbol,p['asof'],p['scenario'],p['start'])
                        match,skip=buy_match(view,p['asof'],p['lookback'])
                        gates=funnel(view,p['lookback'])
                    with self.lock:
                        for category in ('events','rejections'):
                            dest=self.jobs[ident]['funnel'][category]
                            for key,value in gates[category].items():dest[key]=dest.get(key,0)+value
                        dest=self.jobs[ident]['funnel']['stocks']
                        for key in ('no_long_signal','structure_interrupted','has_n','has_squeeze'):
                            dest[key]=dest.get(key,0)+gates[key]
                        if not match and not skip and not gates['no_long_signal']:
                            dest['held_or_not_fresh']=dest.get('held_or_not_fresh',0)+1
                        if match: self.jobs[ident]['results'].append(dict(match,name=stock['name']))
                        if skip: self.jobs[ident]['stale']+=1
                except (ValueError,FileNotFoundError) as exc:
                    with self.lock:
                        self.jobs[ident]['failed']+=1
                        if len(self.jobs[ident]['errors'])<30: self.jobs[ident]['errors'].append(dict(symbol=symbol,error=str(exc)))
                with self.lock:
                    self.jobs[ident]['processed']+=1
                    self.jobs[ident]['performance']['elapsed_seconds']=perf_counter()-started
                    self._publish(ident)  # Wake the UI before computing the next stock.
            if p['source']=='tdx':
                if fingerprint(repo.tdx.root/'T0002/hq_cache/gbbq')!=action_hash: raise RuntimeError('除权文件发生变化，结果作废')
                for symbol,version in versions.items():
                    stat=repo.tdx._path(symbol).stat()
                    if (stat.st_mtime_ns,stat.st_size)!=version: raise RuntimeError('行情文件发生变化，结果作废')
            with self.lock:
                self.jobs[ident].update(status='cancelled' if stop.is_set() else 'completed',current=None)
                self._publish(ident)
        except Exception as exc:
            with self.lock:
                self.jobs[ident].update(status='failed',error=str(exc),results=[],current=None)
                self._publish(ident)

    def _publish(self,ident):
        """Caller holds self.lock; notifications never hold the calculation lock."""
        self.jobs[ident]['revision']+=1
        self.changed.notify_all()

    def get(self,ident,after=None,*,timeout=20):
        with self.changed:
            if ident not in self.jobs: raise ValueError('扫描任务不存在或服务已重启')
            if after is not None:
                if type(after) is not int or not 0<=after<=self.jobs[ident]['revision']:
                    raise ValueError('invalid scan revision')
                if not 0<=timeout<=20: raise ValueError('invalid scan wait timeout')
                # Condition.wait_for releases the lock. A slow next stock cannot
                # prevent a reader from receiving already-matched results.
                self.changed.wait_for(lambda:ident not in self.jobs or self.jobs[ident]['revision']>after or
                    self.jobs[ident]['status'] not in ('running','cancelling'),timeout=timeout)
                if ident not in self.jobs: raise ValueError('扫描任务不存在或已清理')
            return deepcopy(self.jobs[ident])

    def cancel(self,ident):
        with self.lock:
            if ident not in self.jobs: raise ValueError('扫描任务不存在')
            if self.jobs[ident]['status']=='running':
                self.stops[ident].set();self.jobs[ident]['status']='cancelling'
                self._publish(ident)
            return deepcopy(self.jobs[ident])
