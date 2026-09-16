"""Read-only local A-share browser; separate from sealed backtest universes."""
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
import re
import time

from wavequant.infrastructure.market_data.tdx import RECORD, read_day
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.infrastructure.filesystem.project_paths import project_path


def is_stock(market, code):
    return bool(re.fullmatch({'sh': r'(60|68)\d{4}', 'sz': r'(00|30)\d{4}',
                              'bj': r'(43|82|83|87|88|92)\d{4}'}[market], code))


def read_names(path):
    """TDX TNF cache: 50-byte header, modern 360 or legacy 314 byte records."""
    data=path.read_bytes()[50:]
    if not data: return {}
    for size,offset,width in ((360,31,40),(314,23,8)):
        if len(data)%size: continue
        result={}
        for i in range(0,len(data),size):
            record=data[i:i+size]
            code=record[:6].decode('ascii',errors='ignore')
            if not re.fullmatch(r'\d{6}',code): continue
            try: name=record[offset:offset+width].split(b'\0',1)[0].decode('gbk',errors='strict').strip()
            except UnicodeError: continue  # A malformed name must not discard the market.
            if name: result[code]=''.join(name.split())  # TDX pads e.g. 五 粮 液.
        if result: return result
    raise ValueError('unsupported TNF layout')


class TdxBrowser:
    def __init__(self, root, cache=None):
        from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester
        self.root=Path(root).resolve(); self._catalog=None; self._loaded_at=0
        self.artifacts=ArtifactCache(cache or project_path('data', 'cache'))
        self.engine=TdxBacktester._engine_hashes()

    def stock_metadata(self,symbol):
        """A single-stock request need not reopen thousands of other .day files."""
        market,code=symbol.split('.')
        path=self.root/'T0002/hq_cache'/f'{market}s.tnf'
        try:
            s=path.stat()
            names=_names(str(path),s.st_mtime_ns,s.st_ctime_ns,s.st_size)
        except FileNotFoundError:
            names={}
        # A corrupt existing name cache is not evidence of non-ST status.
        return dict(symbol=symbol,name=names.get(code))

    def catalog(self):
        if self._catalog is not None and time.monotonic()-self._loaded_at<10: return self._catalog
        stocks=[]; warnings=[]; latest=[]
        for market in ('sh','sz','bj'):
            names={}; file=self.root/'T0002/hq_cache'/f'{market}s.tnf'
            if file.is_file():
                try:
                    s=file.stat(); names=_names(str(file),s.st_mtime_ns,s.st_ctime_ns,s.st_size)
                except (ValueError,UnicodeError,OSError) as exc: warnings.append(f'{market} 名称缓存不可读：{exc}')
            paths={p.stem[2:]:p for p in (self.root/'vipdoc'/market/'lday').glob(f'{market}*.day')
                   if is_stock(market,p.stem[2:])}
            for code in sorted(set(paths)|{c for c in names if is_stock(market,c)}):
                row=dict(symbol=f'{market}.{code}',name=names.get(code),source='tdx',bar_count=0,
                         first=None,last=None,has_data=False,status='missing_daily')
                path=paths.get(code)
                if path:
                    try:
                        s=path.stat();size=s.st_size
                        first,last=_daily_header(str(path),s.st_mtime_ns,s.st_ctime_ns,size)
                        row.update(bar_count=size//RECORD.size,first=first,last=last,has_data=True,status='available')
                        latest.append(last)
                    except (OSError,ValueError) as exc: row.update(status='invalid_daily',error=str(exc))
                stocks.append(row)
        self._catalog=dict(stocks=stocks,source='Tongdaxin local .tnf + .day',available=bool(stocks),
                           latest=max(latest) if latest else None,warnings=warnings,
                           with_daily=sum(s['has_data'] for s in stocks),scope='local_SH_SZ_BJ_A_shares',
                           notice='沪深北 A 股本机目录，非实时行情；未下载日线的股票不可打开。名称为当前通达信缓存，不代表历史名称。')
        self._loaded_at=time.monotonic()
        return self._catalog

    def _path(self,symbol):
        if not re.fullmatch(r'(sh|sz|bj)\.\d{6}',symbol): raise ValueError('invalid TDX symbol')
        market,code=symbol.split('.')
        if not is_stock(market,code): raise ValueError('not an A-share symbol')
        path=(self.root/'vipdoc'/market/'lday'/f'{market}{code}.day').resolve()
        if not path.is_relative_to(self.root): raise ValueError('TDX path escaped root')
        if not path.is_file(): raise ValueError('通达信尚未下载该股票日线')
        return path

    def bars(self,symbol,asof):
        if date.fromisoformat(asof).isoformat()!=asof: raise ValueError('use YYYY-MM-DD date')
        path=self._path(symbol)
        digest=fingerprint(path)
        result=self._bars(symbol,asof,digest)
        if fingerprint(path)!=digest: raise ValueError('通达信日线正在更新，请稍后重试')
        return result

    @lru_cache(maxsize=16)
    def _bars(self,symbol,asof,digest):
        rows=read_day(self._path(symbol))
        values=[Bar(datetime.combine(r['date'],datetime.min.time()),symbol,
                    *(r[k] for k in ('open','high','low','close','volume')),False,False)
                for r in rows if r['date'].isoformat()<=asof]
        if not values: raise ValueError('该回放日期之前没有日线数据')
        if fingerprint(self._path(symbol))!=digest: raise ValueError('通达信日线正在更新，请稍后重试')
        return values

    def view(self,symbol,asof):
        bars=self.bars(symbol,asof)
        return dict(symbol=symbol,asof=asof,result_scope='tdx',price_basis='raw_unadjusted',
                    sessions=[b.timestamp.date().isoformat() for b in self.bars(symbol,'9999-12-31')],
                    bars=[dict(time=b.timestamp.date().isoformat(),open=b.open,high=b.high,low=b.low,
                               close=b.close,raw_close=b.close,volume=b.volume,factor=1) for b in bars],
                    markers=[],signals=[],orders=[],trades=[],curve=[],metrics=None,
                    evidence='通达信原始不复权日线，仅行情与讲义绘图；未执行回测，除权缺口可能影响形态。')

    def theory(self,symbol,asof):
        from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester
        path=self._path(symbol);digest=fingerprint(path)
        engine=TdxBacktester._engine_hashes()
        if engine!=self.engine: raise ValueError('画线代码已变更，请重启服务后重试')
        key=dict(symbol=symbol,asof=asof,day_sha256=digest,engine=engine,price_basis='raw_unadjusted')
        with self.artifacts.lock(self.artifacts.key('raw_theory',key)):
            result=self.artifacts.get('raw_theory',key)
            if result is None:
                result=self._theory(symbol,asof,digest)
                if fingerprint(path)!=digest or TdxBacktester._engine_hashes()!=engine:
                    self._theory.cache_clear()
                    raise ValueError('行情或代码正在更新，请重启后重试')
                self.artifacts.put('raw_theory',key,result)
            if fingerprint(path)!=digest or TdxBacktester._engine_hashes()!=engine:
                raise ValueError('行情或代码正在更新，请重启后重试')
            return result

    @lru_cache(maxsize=8)
    def _theory(self,symbol,asof,digest):
        bars=self.bars(symbol,asof); drawing=lecture_drawing(bars)
        first=reversal_trends(drawing,bars);second=secondary_trends(first,bars)
        return dict(asof=asof,points=[],polyline_segments=[],events=[],shapes=[],counts={},
                    lecture_drawing=drawing,reversal_trends=first,secondary_trends=second,
                    tertiary_trends=tertiary_trends(second,bars),interrupted=False,
                    computed_from='tdx_raw_prefix_display_only',price_basis='raw_unadjusted')


@lru_cache(maxsize=12)
def _names(path,mtime,ctime,size):
    return read_names(Path(path))


@lru_cache(maxsize=12000)
def _daily_header(path,mtime,ctime,size):
    if not size or size%RECORD.size: raise ValueError('日线记录为空或不完整')
    with Path(path).open('rb') as f:
        first=RECORD.unpack(f.read(RECORD.size))[0]
        f.seek(-RECORD.size,2);last=RECORD.unpack(f.read(RECORD.size))[0]
    first=datetime.strptime(str(first),'%Y%m%d').date().isoformat()
    last=datetime.strptime(str(last),'%Y%m%d').date().isoformat()
    if first>last: raise ValueError('日期倒序')
    return first,last
