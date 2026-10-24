/** 精简趋势DTO的显示适配；仅解析来源索引，不识别趋势、不重组已计算端点。 */
export function isTrendState(value){return value?.schemaVersion===1&&value?.state?.data&&typeof value.dataVersion==="string";}
export function trendStateView(raw,snapshotRaw,{parseState=globalThis.window?.wavequantParseTrendAnalysis,parseBars=globalThis.window?.wavequantParseTrendBars}={}){
    if(!parseState||!parseBars)throw new Error("趋势数据契约解析器尚未就绪");
    const dto=parseState(raw),snapshot=parseBars(snapshotRaw),s=dto.state;
    if(dto.symbol!==snapshot.symbol||dto.timeframe!==snapshot.timeframe||dto.dataVersion!==snapshot.dataVersion||s.currentIndex!==snapshot.bars.length)throw new Error("趋势与行情快照不一致，请重新计算");
    const bars=snapshot.bars;
    if(bars.some((b,i)=>i>0&&b.time<=bars[i-1].time))throw new Error("趋势行情顺序无效");
    const rawLevels=[s.data.polyline,s.data.level1,s.data.level2,s.data.level3];
    const levels=rawLevels.map((points,level)=>points.map((point)=>{
        const source=level===0?point:levelsSource(level,point.sourceIndex);
        const bar=bars[source.barIndex],known=bars[point.knownIndex];
        if(!bar||!known)throw new Error("趋势行情索引越界");
        return {index:source.barIndex,barIndex:source.barIndex,time:bar.time,value:source.kind==="H"?bar.high:bar.low,kind:source.kind,label:source.kind,ordinal:source.ordinal,path:point.path??0,source_path:level?"level"+(level-1)+"-lecture-"+(rawLevels[level-1][point.sourceIndex].path??0):null,
            state:point.confirmed?(level===0?"confirmed":"reversal"):"developing",confirmed_at:known.time,available_at:known.time,trend_level:level,referenceOnly:point.referenceOnly===true};
    }));
    // 向下逐级读取同一引用，不通过日期或价格猜测对应点。
    function levelsSource(level,index){let point=rawLevels[level-1]?.[index];if(!point)throw new Error("趋势来源索引越界");while(--level>0){point=rawLevels[level-1]?.[point.sourceIndex];if(!point)throw new Error("趋势来源索引越界");}return point;}
    const names=["lecture_drawing","reversal_trends","secondary_trends","tertiary_trends"],kinds=["lecture","reversal","secondary","tertiary"];
    const theory={asof:bars.at(-1)?.time??snapshot.asof,complete:dto.complete,unavailable_rules:dto.unavailableRules,calculation_mode:"first_level_only",trading_applicable:false,
        computed_from:"rust_trend_state_v1",points:[],polyline_segments:[],events:[],shapes:[],counts:{rows:bars.length},event_counts:{},interrupted:false,note:"仅计算折线和一级趋势，交易未运行。"};
    levels.forEach((points,level)=>{
        const groups=new Map();for(const p of points){if(p.referenceOnly)continue;const group=groups.get(p.path)??[];group.push(p);groups.set(p.path,group);}
        const strokes=[],developing=[];
        for(const [path,rows]of groups){
            const id="level"+level+"-lecture-"+path,sourcePaths=[...new Set(rows.map(p=>p.source_path).filter(Boolean))],sourcePath=sourcePaths.at(-1)??null;
            const stroke={id,kind:kinds[level],trend_level:level,source_path:sourcePath,source_paths:sourcePaths,leg_confirmation_policy:"high_low_point_geometry_v113",points:rows};
            const fixed=rows.filter(p=>p.state!=="developing");
            if(fixed.length)strokes.push({...stroke,points:fixed});
            if(fixed.length!==rows.length)developing.push({...stroke,kind:kinds[level]+"-developing",display_only:true});
        }
        theory[names[level]]={enabled:s.enabled[level],trend_level:level,source_level:level?level-1:null,state:s.trendState[level],strokes,developing_strokes:developing,
            structural_points:points.filter(p=>!p.referenceOnly&&p.state!=="developing"),confirmed_wave_count:points.filter(p=>!p.referenceOnly&&p.state!=="developing").length,
            source_strokes:level?theory[names[level-1]].strokes:[],events:[],issues:[],blocked_at:null,n_target_trend_confirmation_enabled:false,
            abc_observations:[],key_transitions:[],alternation_segments:[],qualification_events:[],bullish_turn_signals:[],bear_to_bull_highs:[],post_alternation_bull_highs:[],key_transition_count:0};
        if(level===0){theory.points=points.filter(p=>!p.referenceOnly&&p.state!=="developing");theory.polyline_segments=strokes.map(stroke=>({id:stroke.id,points:stroke.points}));}
    });
    return {symbol:dto.symbol,asof:bars.at(-1)?.time??snapshot.asof,timeframe:dto.timeframe,timeframe_label:{"1d":"日线","1w":"周线","1mo":"月线","3mo":"季线","1y":"年线"}[dto.timeframe],
        data_version:dto.dataVersion,price_basis:snapshot.priceBasis,bars,sessions:bars.map(b=>b.time),result_scope:"stock",data_source:snapshot.source,resolved_source:snapshot.source,providers:[snapshot.source],source_fallback:false,supplemented_bars:0,
        complete:dto.complete,unavailable_rules:dto.unavailableRules,calculation_mode:"first_level_only",trendState:dto.state,theory,
        backtest:{status:"not_run_structure_only",start:bars[0]?.time??null,end:bars.at(-1)?.time??null,source:{provider:snapshot.source}},
        metrics:{},markers:[],signals:[],orders:[],trades:[],audit:[],curve:[],equity:[],open_positions:[],holding_drawdowns:[],counts:{events:{},rejection_reasons:{}},comparison:{},
        performance:{analysis_elapsed_seconds:dto.timing.analysisMs/1000,elapsed_seconds:dto.timing.analysisMs/1000,data_request_seconds:dto.timing.dataRequestMs/1000,total_elapsed_seconds:dto.timing.totalMs/1000,timing_scope:"analysis"}};
}
