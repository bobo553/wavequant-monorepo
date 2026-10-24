//! 草稿驱动的逐 K 线调度。四个层级都属于 identity.timeframe 的同一行情序列。
//! level 0 为折线，1/2/3 为逐级趋势；层级推进不聚合或切换日、周、月周期。
//! 结构计算与交易资格分离，买点传播只消费当前可见 K 线的缓存。
use std::{collections::BTreeMap,time::Instant,array, str::FromStr, sync::{Arc,atomic::{AtomicBool, Ordering}}};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use super::{kernel::{self, KernelBar, KernelFrame, KernelOptions, TrendState}, trading::{self, RuleCandle, qualification::TradingEligibility, FrameTradeEvaluation}};

pub const VERSION: u32 = 14;
// 用户临时要求仅计算折线；恢复其他等级时关闭此开关。
pub const STRUCTURE_ONLY:bool=true;
const MAX_BARS: usize = 250_000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DataIdentity {
    pub symbol: String,
    pub source_version: String,
    pub adjustment: String,
    /// K 线周期，与结构 level 独立；一个分析器内四层共享此周期。
    pub timeframe: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisBar {
    pub index: usize,
    pub time: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: Decimal,
    #[serde(default = "one")]
    pub adjustment_factor: Decimal,
    #[serde(default)]
    pub raw_trading_active: Option<bool>,
    #[serde(default)]
    pub raw_is_st: Option<bool>,
}

fn one()->Decimal {Decimal::ONE}

impl AnalysisBar {
    fn kernel(&self) -> KernelBar {
        KernelBar { index:self.index, time:self.time.clone(), open:self.open, high:self.high, low:self.low, close:self.close }
    }

    fn candle(&self, symbol: &str) -> Result<RuleCandle, String> {
        let decimal = |price: f64| Decimal::from_str(&price.to_string()).map_err(|_| "price cannot be represented exactly as a decimal".to_string());
        Ok(RuleCandle {
            symbol:symbol.into(), timestamp:self.time.clone(),
            open:decimal(self.open)?, high:decimal(self.high)?, low:decimal(self.low)?, close:decimal(self.close)?,
            volume:self.volume, adjustment_factor:self.adjustment_factor,
            raw_trading_active:self.raw_trading_active, raw_is_st:self.raw_is_st,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct AnalyzerConfig {
    /// 只控制各级买点传播，不取消对应级别的结构事实。
    pub enable_secondary_reversal: [bool; 4],
    pub kernel: KernelOptions,
    pub n_target_trend_confirmation_enabled: bool,
    pub trading: trading::day::TradingDayConfig,
    pub risk:trading::risk::RiskConfig,
}

impl Default for AnalyzerConfig {
    fn default() -> Self { Self { enable_secondary_reversal:[true;4], kernel:KernelOptions::default(),
        n_target_trend_confirmation_enabled:false,trading:trading::day::TradingDayConfig::lecture(),risk:Default::default() } }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TrendAnalyzerState {
    pub version: u32,
    /// 下一根尚未处理的 K 线位置；不与回溯起点复用。
    pub current_index: usize,
    /// 每一项是对应级别 points 数组内的位置，不是原K线下标。
    pub trend_data: [Vec<usize>; 4],
    /// 折线、一级、二级、三级分别使用本级数组内的前趋势起点位置。
    pub dynamic_data_index: [usize; 4],
    pub identity: DataIdentity,
    pub config: AnalyzerConfig,
    pub prefix_digest: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisRequest {
    pub identity: DataIdentity,
    pub bars: Vec<AnalysisBar>,
    #[serde(default)]
    pub config: AnalyzerConfig,
    #[serde(default)]
    pub state: Option<TrendAnalyzerState>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TrendExecutionResult {
    /// 0=折线，1=一级趋势，2=二级趋势，3=三级趋势。
    pub level: u8,
    pub run_next_level: bool,
    pub next_offset: usize,
    pub has_new_source: bool,
    pub gates: FrameTradeEvaluation,
    /// 本函数计算的单层数据；next_offset 是上一个趋势起点在 data.points 中的位置。
    pub data: KernelFrame,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalysisResponse {
    pub state: TrendAnalyzerState,
    pub levels: [KernelFrame; 4],
    pub last_execution: Vec<TrendExecutionResult>,
    pub cache_status: String,
    #[serde(default)]
    pub structure_cache: StructureCacheStats,
    pub complete: bool,
    pub unavailable_rules: Vec<String>,
    pub daily_trading: Option<trading::day::TradingDayEvaluation>,
}

/// 受信任应用缓存的精确前缀身份；HTTP 提交的状态不能提供结构事实。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StructureCacheIdentity {
    pub analyzer_version: u32,
    pub identity: DataIdentity,
    pub config: AnalyzerConfig,
    pub through_index: usize,
    pub prefix_digest: String,
}

/// 缓存只返回本内核已完成的四层结果；交易、持仓和资金状态不属于此端口。
pub trait StructureCache: Send + Sync {
    fn get(&self,key:&StructureCacheIdentity)->Option<[KernelFrame;4]>;
    fn put(&self,key:&StructureCacheIdentity,frames:&[KernelFrame;4]);
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StructureCacheStats {
    pub enabled: bool,
    pub hits: usize,
    pub misses: usize,
}

/// 原始结构按来源版本复用；当日价格许可在独立阶段更新，不缓存成结构点。
struct LevelCache { source_digest: String, frame: KernelFrame }

pub struct TrendAnalyzer {
    bars: Vec<AnalysisBar>,
    kernel_bars: Vec<KernelBar>,
    candles: Vec<RuleCandle>,
    state: TrendAnalyzerState,
    prefix_hasher: Sha256,
    polyline_cursor: kernel::PolylineCursor,
    trend_cursors: [kernel::TrendCursor;4],
    promotion_receipts: [Vec<kernel::PromotionReceipt>;4],
    level_versions: [Option<String>;4],
    caches: [Option<LevelCache>;4],
    frames: [KernelFrame;4],
    array_frames:Option<[kernel::arena::RefFrame;4]>,
    array_compat:std::sync::OnceLock<[KernelFrame;4]>,
    eligibility: [TradingEligibility;4],
    source_changed: [bool;4],
    last_execution: Vec<TrendExecutionResult>,
    evaluations: [Option<FrameTradeEvaluation>;4],
    prepared_at: Option<usize>,
    cache_status: String,
    unavailable_rules: Vec<String>,
    trading_state: trading::day::TradingDayState,
    daily_trading: Option<trading::day::TradingDayEvaluation>,
    structure_cache: Option<Arc<dyn StructureCache>>,
    structure_cache_stats: StructureCacheStats,
    phase_timings:BTreeMap<String,f64>,
}

impl TrendAnalyzer {
    pub fn new(mut request: AnalysisRequest) -> Result<Self, String> {
        request.config.trading.n_target_trend_confirmation_enabled=request.config.n_target_trend_confirmation_enabled;
        request.config.trading.ordinary_trend_completion_enabled=request.config.kernel.ordinary_trend_completion_enabled;
        request.config.trading.validate()?;
        if request.bars.len() > MAX_BARS { return Err("analysis exceeds 250000 bars".into()); }
        if [&request.identity.symbol,&request.identity.source_version,&request.identity.adjustment,&request.identity.timeframe]
            .iter().any(|value| value.trim().is_empty() || value.len()>256) {
            return Err("analysis requires bounded symbol/source_version/adjustment/timeframe".into());
        }
        for (position, bar) in request.bars.iter().enumerate() {
            if bar.index != position { return Err("bar index must equal its position in the complete input".into()); }
        }
        let kernel_bars: Vec<_> = request.bars.iter().map(AnalysisBar::kernel).collect();
        kernel::validate_bars(&kernel_bars)?;
        let candles: Vec<_> = request.bars.iter().map(|bar|bar.candle(&request.identity.symbol)).collect::<Result<_,_>>()?;
        trading::validate_bars(&candles)?;
        let initial_hash = Sha256::new();
        let mut cache_status = "cold".to_string();
        if let Some(saved) = &request.state {
            cache_status = if saved.version != VERSION || saved.identity != request.identity || saved.config != request.config {
                "invalidated_identity_or_rules".into()
            } else if saved.current_index > request.bars.len() {
                "invalidated_truncated_input".into()
            } else {
                let mut digest = Sha256::new();
                for bar in &request.bars[..saved.current_index] { hash_bar(&mut digest,bar)?; }
                if hash_hex(digest) != saved.prefix_digest {
                    "invalidated_revised_input".into()
                } else {
                    // HTTP提交的state不能成为交易资格或趋势事实。重放可见前缀恢复内部缓存，
                    // 不信任客户端提交的起点数组，也不接受客户端声明“已经确认”的结构。
                    "checkpoint_prefix_replayed".into()
                }
            };
        }
        let mut trading_state=trading::day::TradingDayState::default();trading_state.risk.config=request.config.risk.clone();
        Ok(Self {
            state:TrendAnalyzerState {
                version:VERSION,current_index:0,trend_data:array::from_fn(|_|Vec::new()),
                dynamic_data_index:[0;4],identity:request.identity,config:request.config,
                prefix_digest:hash_hex(initial_hash.clone()),
            },
            bars:request.bars,kernel_bars,candles,prefix_hasher:initial_hash,polyline_cursor:Default::default(),trend_cursors:array::from_fn(|_|Default::default()),
            promotion_receipts:array::from_fn(|_|Vec::new()),level_versions:array::from_fn(|_|None),
            caches:array::from_fn(|_|None),frames:array::from_fn(|level|KernelFrame::empty(level as u8)),
            array_frames:None,array_compat:std::sync::OnceLock::new(),
            eligibility:array::from_fn(|_|TradingEligibility::default()),
            source_changed:[false;4],last_execution:Vec::new(),evaluations:array::from_fn(|_|None),prepared_at:None,cache_status,
            unavailable_rules:Vec::new(),trading_state,daily_trading:None,
            structure_cache:None,structure_cache_stats:Default::default(),phase_timings:BTreeMap::new(),
        })
    }

    /// 在原实例追加后只消费新 K 线；旧前缀修改必须新建分析器。
    pub fn append(&mut self, additional: Vec<AnalysisBar>) -> Result<(),String> {
        if self.bars.len().saturating_add(additional.len()) > MAX_BARS { return Err("analysis exceeds 250000 bars".into()); }
        let start=self.bars.len();
        let mut next_kernel = self.kernel_bars.last().cloned().into_iter().collect::<Vec<_>>();
        let mut next_candles = self.candles.last().cloned().into_iter().collect::<Vec<_>>();
        for (offset,bar) in additional.iter().enumerate() {
            if bar.index != start+offset { return Err("appended bars must extend contiguous input".into()); }
            next_kernel.push(bar.kernel());
            next_candles.push(bar.candle(&self.state.identity.symbol)?);
        }
        kernel::validate_bars(&next_kernel)?;
        trading::validate_bars(&next_candles)?;
        let boundary=usize::from(start>0);
        self.kernel_bars.extend(next_kernel.into_iter().skip(boundary));
        self.candles.extend(next_candles.into_iter().skip(boundary));
        self.bars.extend(additional);
        Ok(())
    }

    pub fn run_main_process(&mut self) -> Result<(),String> { self.run_to_end(None) }

    fn run_to_end(&mut self,cancelled:Option<&AtomicBool>)->Result<(),String> {
        while self.state.current_index < self.bars.len() {
            if cancelled.is_some_and(|flag|flag.load(Ordering::Relaxed)) { return Err("analysis cancelled".into()); }
            self.process_next()?;
        }
        self.state.prefix_digest=hash_hex(self.prefix_hasher.clone());
        Ok(())
    }

    /// 回放、图表与实时追加消费同一个逐日入口和同一份已计算结果。
    pub fn process_next(&mut self)->Result<bool,String> {
        if self.state.current_index>=self.bars.len(){return Ok(false);}
        let asof=self.state.current_index;
        self.last_execution.clear();
        self.prepare_structure(asof)?;
        // 结构函数已逐层返回；交易结果只在整条结构链完成后附加一次。
        for level in 0..=if STRUCTURE_ONLY{1}else{3} {let result=self.describe_execution(level,asof)?;self.last_execution.push(result);}
        hash_bar(&mut self.prefix_hasher,&self.bars[asof])?;
        self.state.current_index=asof+1;
        Ok(true)
    }

    pub fn daily_trading(&self)->Option<&trading::day::TradingDayEvaluation>{self.daily_trading.as_ref()}
    /// 一次传入数组后只推进索引；中途不复制完整折线、不生成交易或快照。
    pub fn advance_polyline_index(&mut self)->Result<bool,String>{
        if !STRUCTURE_ONLY||self.state.config.trading.pivot_mode!="lecture_causal"{return self.process_next();}
        let index=self.state.current_index;if index>=self.bars.len(){return Ok(false);}
        self.polyline_cursor.advance_index(&self.kernel_bars[..=index])?;
        hash_bar(&mut self.prefix_hasher,&self.bars[index])?;
        self.state.current_index=index+1;Ok(true)
    }
    pub fn finish_polyline_array(&mut self)->Result<(),String>{
        if self.state.config.trading.pivot_mode=="lecture_causal"{
            let frame=self.polyline_cursor.snapshot_indices(&self.kernel_bars[..self.state.current_index])?;
            self.state.dynamic_data_index[0]=frame.offset(&self.polyline_cursor.arena,&self.kernel_bars)?;
            let frames=self.array_frames.get_or_insert_with(||array::from_fn(|level|kernel::arena::RefFrame::empty(level as u8)));
            self.source_changed[0]=frames[0].points!=frame.points;frames[0]=frame;self.array_compat.take();
        }
        Ok(())
    }
    /// 各层只接收直接下层的点号，偏移为该下层数组内的前趋势起点。
    pub fn finish_first_level_array(&mut self)->Result<(),String>{
        let Some(asof)=self.state.current_index.checked_sub(1)else{return Ok(());};
        let Some(frames)=self.array_frames.as_mut()else{self.execute_level_function(1,asof)?;return Ok(());};
        let offset=frames[0].offset(&self.polyline_cursor.arena,&self.kernel_bars)?;
        let phase=Instant::now();
        let frame=self.trend_cursors[1].advance_indices(&mut self.polyline_cursor.arena,&self.kernel_bars,
            &frames[0].points,offset,1,asof,&self.state.config.kernel)?;
        *self.phase_timings.entry("level1.trend_kernel".into()).or_default()+=phase.elapsed().as_secs_f64();
        let phase=Instant::now();self.state.dynamic_data_index[1]=frame.offset(&self.polyline_cursor.arena,&self.kernel_bars)?;
        self.source_changed[1]=frames[1].points!=frame.points;frames[1]=frame;self.array_compat.take();
        *self.phase_timings.entry("level1.install".into()).or_default()+=phase.elapsed().as_secs_f64();Ok(())
    }
    pub fn trading_state(&self)->&trading::day::TradingDayState{&self.trading_state}
    pub fn eligibilities(&self)->&[TradingEligibility;4]{&self.eligibility}
    /// 合并已观察分钟的机会消费记录；结构仍只在日收对完整K线推进一次。
    pub fn accept_intraday(&mut self,asof:usize,arbiter:&trading::arbiter::EntryArbiterState,events:&[crate::indexed_json::Value])->Result<(),String>{
        if asof!=self.state.current_index{return Err("minute facts must belong to the next unclosed daily bar".into());}
        for event in events{if trading::qualification::index(event,"bar_index")!=Some(asof){continue;}
            if !self.trading_state.events.contains(event){self.trading_state.events.push(event.clone());}}
        self.trading_state.arbiter=arbiter.clone();Ok(())
    }
    pub fn levels(&self)->&[KernelFrame;4]{
        match &self.array_frames{
            Some(frames)=>self.array_compat.get_or_init(||array::from_fn(|level|self.polyline_cursor.arena.materialize(&frames[level],&self.kernel_bars))),
            None=>&self.frames,
        }
    }
    pub fn set_minor_points(&mut self,points:Vec<super::market_state::strength::ConfirmedPivot>)->Result<(),String>{
        if self.state.current_index!=0{return Err("minor source must be frozen before daily processing".into());}
        if points.len()>MAX_BARS||points.iter().any(|p|p.index>p.confirmed_index||p.price<=Decimal::ZERO)
            ||points.windows(2).any(|p|p[0].index>=p[1].index||p[0].confirmed_index>p[1].confirmed_index){return Err("minor source requires bounded ordered causal points".into());}
        self.trading_state.set_minor_points(Some(points));Ok(())
    }

    pub fn set_structure_cache(&mut self,cache:Arc<dyn StructureCache>)->Result<(),String>{
        if self.state.current_index!=0||self.prepared_at.is_some(){return Err("structure cache must be bound before processing".into());}
        self.structure_cache=Some(cache);self.structure_cache_stats.enabled=true;Ok(())
    }

    fn compute_structure(&mut self,asof:usize)->Result<[KernelFrame;4],String> {
        let old_frames=self.frames.clone();
        self.promotion_receipts=array::from_fn(|_|Vec::new());
        let polyline=self.execute_polyline_function(asof)?;
        let mut parent=polyline.data;
        let mut offset=polyline.next_offset;
        for level in 1..=3 {
            let result=self.execute_level_from(level,asof,&parent,offset)?;
            // 下一级只在本函数返回后接手父级数据及全局起点。
            parent=result.data;offset=result.next_offset;
        }
        // 上级严格突破产生新来源版本时，重放相应来源版本；普通日期不重复推进。
        let mut converged=false;
        for _ in 0..64 {
            let mut dirty=[false;4];
            for upper in 2..=3 {for lower in 1..upper {
                for receipt in kernel::promotion_receipts(&self.frames[upper],lower as u8)?{
                    if !self.promotion_receipts[lower].contains(&receipt){
                        self.promotion_receipts[lower].push(receipt);dirty[lower]=true;
                    }
                }
            }}
            if !dirty.iter().any(|changed|*changed){converged=true;break;}
            for level in 1..=3 {
                if dirty[level]{
                    let result=self.execute_level_function(level,asof)?;
                    if level<3&&result.run_next_level{dirty[level+1]=true;}
                }
            }
        }
        if !converged{return Err("trend promotion convergence exceeds bounded version budget".into());}
        // 当日确认只消费各函数已经返回的数据，不再运行结构判定。
        for level in 0..=3 {
            kernel::attach_confirmations(&mut self.frames[level],&self.kernel_bars[..=asof])?;
            kernel::attach_failed_breakouts(&mut self.frames[level],&self.candles[..=asof])?;
            kernel::attach_bearish_structure_breaks(&mut self.frames[level],&self.candles[..=asof])?;
        }
        for level in 1..=3 {
            let source=self.frames[level-1].clone();
            kernel::attach_source_bearish_exits(&mut self.frames[level],&source,&self.candles[..=asof])?;
        }
        let current_polyline=self.frames[0].clone();
        for level in 0..=3 {
            kernel::reconcile_lifecycle_sources(&old_frames[level],&mut self.frames[level],
                Some(&old_frames[0]),Some(&current_polyline),asof)?;
        }
        Ok(self.frames.clone())
    }
    fn prepare_structure(&mut self,asof:usize)->Result<(),String> {
        if self.prepared_at==Some(asof){return Ok(());}
        if STRUCTURE_ONLY {
            // 当前只开放折线和一级；更高等级及交易继续停用。
            self.execute_polyline_function(asof)?;
            self.execute_level_function(1,asof)?;
            for level in 2..=3{self.frames[level]=KernelFrame::empty(level as u8);self.source_changed[level]=false;}
            let evaluations:Vec<_>=(0..4).map(|_|FrameTradeEvaluation{alternation_ended:false,in_qualification_range:false,
                trade_point:false,unavailable_rules:Vec::new(),n_observations:Vec::new(),events:Vec::new(),
                entry_rejections:vec!["polyline_only_mode".into()],exit_decisions:Vec::new()}).collect();
            self.evaluations=array::from_fn(|level|Some(evaluations[level].clone()));
            self.daily_trading=Some(trading::day::TradingDayEvaluation{frames:evaluations,entry_candidates:Vec::new(),events:Vec::new(),
                unavailable_rules:Vec::new(),counts:Default::default(),trading_applicable:false});
            self.prepared_at=Some(asof);
            return Ok(());
        }
        let cache_identity=if self.structure_cache.is_some(){
            let mut prefix=self.prefix_hasher.clone();hash_bar(&mut prefix,&self.bars[asof])?;
            Some(StructureCacheIdentity{analyzer_version:VERSION,identity:self.state.identity.clone(),
                config:self.state.config.clone(),through_index:asof,prefix_digest:hash_hex(prefix)})
        }else{None};
        let cached=cache_identity.as_ref().and_then(|key|self.structure_cache.as_ref().and_then(|cache|cache.get(key)))
            .filter(|frames|cached_frames_visible(frames,&self.kernel_bars,asof));
        let reused=cached.is_some();
        let old_frames=self.frames.clone();
        let frames=if let Some(frames)=cached{
            self.structure_cache_stats.hits+=1;frames
        }else{
            if cache_identity.is_some(){self.structure_cache_stats.misses+=1;}
            self.compute_structure(asof)?
        };
        for level in 0..=3 {self.source_changed[level]=old_frames[level].points!=frames[level].points
            ||old_frames[level].publication!=frames[level].publication;}
        let mut eligibility=self.eligibility.clone();
        let mut trading_state=self.trading_state.clone();
        let daily=trading::day::prepare_trading_day(&self.candles[..=asof],&frames,asof,
            &mut eligibility,&self.state.identity.timeframe,&mut trading_state,&self.state.config.trading)?;
        let mut evaluations=array::from_fn(|_|None);
        for level in 0..=3 {
            let gates=daily.frames.get(level).cloned().ok_or("missing shared level trading result")?;
            for issue in &gates.unavailable_rules {
                if !self.unavailable_rules.contains(issue) {self.unavailable_rules.push(issue.clone());}
            }
            evaluations[level]=Some(gates);
        }
        if !reused {
            if let (Some(cache),Some(key))=(&self.structure_cache,&cache_identity){cache.put(key,&frames);}
        }
        self.frames=frames;
        self.eligibility=eligibility;
        for issue in &daily.unavailable_rules {
            if !self.unavailable_rules.contains(issue){self.unavailable_rules.push(issue.clone());}
        }
        self.trading_state=trading_state;
        self.daily_trading=Some(daily);
        self.evaluations=evaluations;
        self.prepared_at=Some(asof);
        // 包含关系断路是已计算出的结构事实，保留在 frame.issues/blocked_at。
        // 不把历史断路累计成规则不可用，否则后续恢复也会永久阻止结果发布。
        Ok(())
    }

    pub fn execute_polyline_function(&mut self,asof:usize)->Result<TrendExecutionResult,String> {
        if asof!=self.state.current_index||asof>=self.bars.len(){return Err("polyline requires the current visible bar".into());}
        if self.array_frames.is_some(){self.frames=self.levels().clone();self.array_frames=None;self.array_compat.take();}
        let frame=if self.state.config.trading.pivot_mode=="lecture_causal"{
            self.polyline_cursor.advance(&self.kernel_bars[..=asof])?
        }else{super::pivots::pivot_frame(&self.kernel_bars[..=asof],&self.state.config.trading.pivot_mode,self.state.config.trading.pivot_width)?};
        self.install_structure(0,frame)
    }

    pub fn execute_level_function(&mut self,level:usize,asof:usize)->Result<TrendExecutionResult,String> {
        if !(1..=3).contains(&level) { return Err("trend level must be 1, 2 or 3".into()); }
        let parent=self.frames[level-1].clone();
        let offset=preceding_trend_offset(&parent)?;
        self.execute_level_from(level,asof,&parent,offset)
    }

    pub fn execute_level_from(&mut self,level:usize,asof:usize,parent:&KernelFrame,offset:usize)->Result<TrendExecutionResult,String>{
        if !(1..=3).contains(&level)||parent.level as usize+1!=level||!(asof==self.state.current_index||STRUCTURE_ONLY&&asof.checked_add(1)==Some(self.state.current_index))||asof>=self.bars.len()
            ||offset>parent.points.len()||offset!=preceding_trend_offset(parent)?{
            return Err("trend requires the directly lower level, its array offset and the current visible bar".into());
        }
        let phase=Instant::now();
        let mut source=parent.points.clone();
        let supported:Vec<_>=self.promotion_receipts[level].iter().filter(|receipt|{
            [&receipt.origin,&receipt.attack].iter().all(|reference|source.iter().any(|point|point.order()==reference.order()
                &&point.kind==reference.kind&&point.value==reference.value&&point.time==reference.time&&point.known_index<=receipt.known_index))
        }).cloned().collect();
        kernel::attach_promotion_receipts(&mut source,&supported)?;
        let qualified=(level>1).then_some(parent.publication.as_slice());
        self.phase_timings.insert(format!("level{level}.source_prepare"),phase.elapsed().as_secs_f64());
        let phase=Instant::now();
        let raw_version=digest(&(VERSION,&source,&self.state.config.kernel))?;
        let version=digest(&(&raw_version,&qualified,self.state.config.n_target_trend_confirmation_enabled,asof))?;
        self.phase_timings.insert(format!("level{level}.source_fingerprint"),phase.elapsed().as_secs_f64());
        if self.level_versions[level].as_ref()==Some(&version){return self.structure_execution(level);}
        let phase=Instant::now();
        let mut frame=if let Some(cache)=self.caches[level].as_ref().filter(|cache|cache.source_digest==raw_version){cache.frame.clone()}
            else{self.trend_cursors[level].advance_from(&source,offset,level as u8,asof,&self.state.config.kernel)?};
        *self.phase_timings.entry("level1.trend_kernel".into()).or_default()+=phase.elapsed().as_secs_f64();
        let phase=Instant::now();
        self.caches[level]=Some(LevelCache{source_digest:raw_version,frame:frame.clone()});
        self.phase_timings.insert(format!("level{level}.cache_copy"),phase.elapsed().as_secs_f64());
        let phase=Instant::now();
        kernel::refresh_publication(&mut frame,&source,qualified,&self.candles[..=asof],asof,self.state.config.n_target_trend_confirmation_enabled)?;
        self.phase_timings.insert(format!("level{level}.publication"),phase.elapsed().as_secs_f64());
        self.level_versions[level]=Some(version);
        let phase=Instant::now();
        let result=self.install_structure(level,frame);
        self.phase_timings.insert(format!("level{level}.install_and_return"),phase.elapsed().as_secs_f64());
        result
    }

    fn store_structure(&mut self,level:usize,frame:KernelFrame)->Result<(),String>{
        self.source_changed[level]=self.frames[level].points!=frame.points||self.frames[level].publication!=frame.publication;
        self.state.dynamic_data_index[level]=preceding_trend_offset(&frame)?;
        self.frames[level]=frame;Ok(())
    }
    fn install_structure(&mut self,level:usize,frame:KernelFrame)->Result<TrendExecutionResult,String>{
        self.store_structure(level,frame)?;
        self.structure_execution(level)
    }
    fn structure_execution(&self,level:usize)->Result<TrendExecutionResult,String>{
        Ok(TrendExecutionResult{level:level as u8,run_next_level:self.source_changed[level],next_offset:preceding_trend_offset(&self.frames[level])?,
            has_new_source:if level==0{self.source_changed[0]}else{self.source_changed[level-1]},data:self.frames[level].clone(),
            gates:FrameTradeEvaluation{alternation_ended:false,in_qualification_range:false,trade_point:false,
                unavailable_rules:Vec::new(),n_observations:Vec::new(),events:Vec::new(),entry_rejections:Vec::new(),exit_decisions:Vec::new()}})
    }

    fn describe_execution(&mut self,level:usize,asof:usize)->Result<TrendExecutionResult,String> {
        if asof != self.state.current_index || asof>=self.bars.len() {
            return Err("execution requires the current visible bar".into());
        }
        let frame=&self.frames[level];
        let has_new_source=if level==0 { self.source_changed[0] } else { self.source_changed[level-1] };
        self.state.trend_data[level].clear();
        // 记录真实结构起点；升级来源与当前层归属分开，不复制父层最后一个数字冒充当前层判定。
        for event in &frame.events {
            if event.known_index<=asof {
                if let Some(origin)=&event.origin {
                    if let Some(position)=point_position(frame,origin).filter(|_|origin.known_index<=asof) {
                        if !self.state.trend_data[level].contains(&position){self.state.trend_data[level].push(position);}
                    }
                }
            }
        }
        if self.state.trend_data[level].is_empty()
            && matches!(frame.state,Some(TrendState::BearBullAlternation | TrendState::Bull)) {
            if !frame.points.is_empty() {
                self.state.trend_data[level].push(0);
            }
        }
        let mut gates=self.evaluations[level].clone().ok_or_else(||"missing visible qualification evaluation".to_string())?;
        // 草稿的全局买卖点门禁消费本日最终仲裁，单层观察候选不直接成为可执行信号。
        gates.trade_point=self.daily_trading.as_ref().is_some_and(|day|day.trading_applicable
            &&(day.entry_candidates.iter().any(|candidate|candidate.fully_executable)
                ||day.events.iter().any(|event|event.get("event").and_then(crate::indexed_json::Value::as_str)==Some("exit_signal")
                    &&trading::qualification::index(event,"bar_index")==Some(asof))));
        for issue in &gates.unavailable_rules {
            if !self.unavailable_rules.contains(issue) { self.unavailable_rules.push(issue.clone()); }
        }
        let reversal=matches!(frame.state,Some(TrendState::BearToBull | TrendState::BullToBear));
        let has_reversal=self.state.config.enable_secondary_reversal[level]
            && gates.unavailable_rules.is_empty()
            && gates.alternation_ended && gates.in_qualification_range && gates.trade_point && reversal;
        self.state.dynamic_data_index[level]=preceding_trend_offset(frame)?;
        if has_reversal {
            let origin=frame.preceding_start.as_ref().ok_or_else(||"reversal has no preceding trend origin".to_string())?;
            if origin.index>asof || origin.known_index>asof { return Err("reversal origin is not yet visible".into()); }
            let position=preceding_trend_offset(frame)?;
            if !self.state.trend_data[level].contains(&position) { self.state.trend_data[level].push(position); }
        }
        Ok(TrendExecutionResult {
            level:level as u8,run_next_level:self.source_changed[level],
            next_offset:preceding_trend_offset(frame)?,has_new_source,gates,data:frame.clone(),
        })
    }

    pub fn export_state(&self)->TrendAnalyzerState {
        let mut state=self.state.clone();
        state.prefix_digest=hash_hex(self.prefix_hasher.clone());
        state
    }
    pub fn get_trend_data(&self,level:usize)->Result<&[usize],String> {
        self.state.trend_data.get(level).map(Vec::as_slice).ok_or_else(||"level must be 0..3".into())
    }
    pub fn get_dynamic_data_index(&self,level:usize)->Result<usize,String> {
        self.state.dynamic_data_index.get(level).copied().ok_or_else(||"level must be 0..3".into())
    }
    pub fn get_current_index(&self)->usize { self.state.current_index }
    pub fn analysis_timings(&self)->&BTreeMap<String,f64>{&self.phase_timings}
    pub fn indexed_response(&self)->super::indexed::IndexedAnalysis{
        let complete=self.state.current_index==self.bars.len()&&self.unavailable_rules.is_empty();
        match &self.array_frames{
            Some(frames)=>super::indexed::IndexedAnalysis::from_arena(self.export_state(),frames,&self.polyline_cursor.arena,&self.kernel_bars,complete,self.unavailable_rules.clone()),
            None=>super::indexed::IndexedAnalysis::from_frames(self.export_state(),&self.frames,complete,self.unavailable_rules.clone()),
        }
    }
    pub fn response(&self)->AnalysisResponse {
        let unavailable_rules=self.unavailable_rules.clone();
        // complete 是本次输入处理状态；源码迁移尚未执行的验收记录独立保留在工程状态中。
        let complete=self.state.current_index==self.bars.len()&&unavailable_rules.is_empty();
        AnalysisResponse {
            state:self.export_state(),levels:self.levels().clone(),last_execution:self.last_execution.clone(),
            cache_status:self.cache_status.clone(),complete,unavailable_rules,daily_trading:self.daily_trading.clone(),
            structure_cache:self.structure_cache_stats.clone(),
        }
    }
}

fn point_position(frame:&KernelFrame,reference:&kernel::KernelPoint)->Option<usize>{
    frame.points.iter().position(|point|point.order()==reference.order()&&point.kind==reference.kind&&point.value==reference.value)
}
fn preceding_trend_offset(frame:&KernelFrame)->Result<usize,String> {
    // point.index 是原K线坐标；偏移必须转换为本级数组位置，不能直接混用。
    match &frame.preceding_start {
        None=>Ok(0),
        Some(origin)=>point_position(frame,origin).ok_or_else(||"preceding trend origin is absent from this level's data array".into()),
    }
}

#[cfg(test)]
mod offset_tests {
    use super::*;
    #[test]
    fn all_levels_return_the_preceding_trend_origin_not_the_latest_endpoint(){
        for level in 0..=3 {
            let mut frame=KernelFrame::empty(level);
            let point=|index|kernel::KernelPoint::new(crate::analysis::kernel::KernelPointData{index,ordinal:0,time:format!("bar-{index}"),kind:kernel::PointKind::L,
                value:10.0,known_index:index,level,path:0,state:kernel::PointState::Confirmed,evidence:crate::indexed_json::Value::Null});
            frame.points=vec![point(4),point(15),point(28)];
            assert_eq!(preceding_trend_offset(&frame).unwrap(),0);
            frame.preceding_start=Some(point(15));
            assert_eq!(preceding_trend_offset(&frame).unwrap(),1);
            frame.candidate=Some(point(35));
            assert_eq!(preceding_trend_offset(&frame).unwrap(),1);
            frame.preceding_start=Some(point(42));
            assert!(preceding_trend_offset(&frame).is_err());
        }
    }
}

fn cached_frames_visible(frames:&[KernelFrame;4],bars:&[KernelBar],asof:usize)->bool {
    let visible=|point:&kernel::KernelPoint|point.index<=point.known_index&&point.known_index<=asof
        &&point.value.is_finite()&&point.value>0.0&&point.level<=3
        &&bars.get(point.index).is_some_and(|bar|bar.time==point.time
            &&point.value==if point.kind==kernel::PointKind::H{bar.high}else{bar.low});
    frames.iter().enumerate().all(|(level,frame)|frame.level as usize==level
        &&frame.blocked_at.is_none_or(|index|index<=asof)
        &&frame.points.iter().chain(frame.publication.iter()).chain(frame.candidate.iter())
            .chain(frame.key.iter()).chain(frame.anchor.iter()).chain(frame.preceding_start.iter()).all(&visible)
        &&frame.events.iter().all(|event|event.known_index<=asof&&visible(&event.point)
            &&event.key.as_ref().is_none_or(&visible)&&event.origin.as_ref().is_none_or(&visible)))
}

pub fn analyze(request:AnalysisRequest)->Result<AnalysisResponse,String> {
    let mut analyzer=TrendAnalyzer::new(request)?;
    analyzer.run_main_process()?;
    Ok(analyzer.response())
}

pub fn analyze_with_cancel(request:AnalysisRequest,cancelled:&AtomicBool)->Result<AnalysisResponse,String> {
    let mut analyzer=TrendAnalyzer::new(request)?;
    analyzer.run_to_end(Some(cancelled))?;
    Ok(analyzer.response())
}

fn hash_bar(digest:&mut Sha256,bar:&AnalysisBar)->Result<(),String> {
    let bytes=crate::indexed_json::to_vec(bar).map_err(|error|error.to_string())?;
    digest.update((bytes.len() as u64).to_le_bytes()); digest.update(bytes); Ok(())
}
fn hash_hex(digest:Sha256)->String { format!("{:x}",digest.finalize()) }
fn digest(value:&impl Serialize)->Result<String,String> {
    let bytes=crate::indexed_json::wire::to_vec(value).map_err(|error|error.to_string())?;
    let mut digest=Sha256::new(); digest.update(bytes); Ok(hash_hex(digest))
}
