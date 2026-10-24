use wavequant_quant_rs::analysis::kernel::{self,KernelBar,KernelOptions,KernelPoint,PointKind,PointState,PolylineCursor,TrendCursor};
use wavequant_quant_rs::analysis::analyzer::{AnalysisBar,AnalysisRequest,AnalyzerConfig,DataIdentity,TrendAnalyzer};
use rust_decimal::Decimal;

fn bars()->Vec<KernelBar>{
    [(10.0,12.0,9.0,11.0),(11.0,14.0,10.0,13.0),(13.0,13.5,11.0,12.0),
     (12.0,15.0,10.5,14.0),(14.0,14.5,12.0,13.0),(13.0,16.0,11.0,15.0),
     (15.0,17.0,13.0,16.0),(16.0,16.5,12.5,14.0),(14.0,18.0,12.0,17.0)]
        .into_iter().enumerate().map(|(index,(open,high,low,close))|KernelBar{
            index,time:format!("2020-01-{:02}",index+1),open,high,low,close}).collect()
}

#[test]
fn polyline_append_preserves_the_cold_prefix_result(){
    let bars=bars();let mut cursor=PolylineCursor::default();
    let mut saved=None;
    for end in 1..=bars.len(){
        let frame=cursor.advance(&bars[..end]).expect("incremental drawing");
        assert_eq!(frame,kernel::polyline(&bars[..end]).expect("cold drawing"));
        if end==4{saved=Some(frame.clone());}
        assert_eq!(cursor.advance(&bars[..end]).expect("same prefix"),frame);
    }
    assert_eq!(saved.unwrap(),kernel::polyline(&bars[..4]).unwrap());
    let mut revised=bars.clone();revised.last_mut().unwrap().close=16.5;
    assert!(cursor.advance(&revised).is_err());
    assert!(cursor.advance(&bars[..3]).is_err());
}

#[test]
fn one_input_array_can_advance_by_index_and_materialize_only_at_the_end(){
    let input=bars();let mut cursor=PolylineCursor::default();let mut index=0;
    while index<input.len(){cursor.advance_index(&input[..=index]).unwrap();index+=1;}
    assert_eq!(cursor.snapshot(&input).unwrap(),kernel::polyline(&input).unwrap());
    assert_eq!(cursor.snapshot(&input).unwrap(),kernel::polyline(&input).unwrap());
}

fn points(level:u8)->Vec<KernelPoint>{
    [8.0,12.0,10.0,15.0,11.0,17.0,9.0,13.0,7.0,11.0,6.0,14.0,10.0,18.0]
        .into_iter().enumerate().map(|(index,value)|KernelPoint::new(wavequant_quant_rs::analysis::kernel::KernelPointData{
            index:index*3+5,ordinal:0,time:format!("2020-01-{:02}",index+1),kind:if index%2==0{PointKind::L}else{PointKind::H},
            value,known_index:index*3+5,level:level-1,path:0,state:PointState::Confirmed,evidence:wavequant_quant_rs::indexed_json::Value::Null})).collect()
}

#[test]
fn all_trend_levels_resume_the_same_state_machine_and_rebuild_revisions(){
    for level in 1..=3{
        let source=points(level);let options=KernelOptions::default();let mut cursor=TrendCursor::default();
        for end in 1..=source.len(){
            let prefix=&source[..end];
            let asof=prefix.last().unwrap().known_index;
            let expected=kernel::trend(prefix,level,asof,&options).unwrap();
            let offset=end.saturating_sub(3);
            assert_eq!(cursor.advance_from(prefix,offset,level,asof,&options).unwrap(),expected);
        }
        let mut revised=source.clone();revised[3].value=14.5;
        let asof=source.last().unwrap().known_index;
        assert_eq!(cursor.advance_from(&revised,10,level,asof,&options).unwrap(),kernel::trend(&revised,level,asof,&options).unwrap());
        assert_eq!(cursor.advance_from(&source[..4],2,level,source[3].known_index,&options).unwrap(),kernel::trend(&source[..4],level,source[3].known_index,&options).unwrap());
        assert!(cursor.advance_from(&source,source.len()+1,level,asof,&options).is_err());
    }
}

#[test]
fn structure_propagation_does_not_require_a_trade_or_daily_strategy(){
    let input=bars().into_iter().map(|bar|AnalysisBar{index:bar.index,time:bar.time,open:bar.open,high:bar.high,
        low:bar.low,close:bar.close,volume:Decimal::from(1000),adjustment_factor:Decimal::ONE,
        raw_trading_active:None,raw_is_st:None}).collect();
    let mut config=AnalyzerConfig::default();config.enable_secondary_reversal=[false;4];
    let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:"sz.002724".into(),source_version:"fixture".into(),
        adjustment:"raw".into(),timeframe:"1w".into()},bars:input,config,state:None}).unwrap();
    let mut propagated=false;
    while analyzer.process_next().unwrap(){
        let result=analyzer.response();
        assert!(!result.daily_trading.as_ref().unwrap().trading_applicable);
        if result.last_execution.first().is_some_and(|step|step.run_next_level){
            propagated=true;
            assert!(result.last_execution.iter().any(|step|step.level==1));
        }
    }
    assert!(propagated);
}

#[test]
fn each_structure_function_returns_before_the_caller_advances_the_next_level(){
    let input=bars().into_iter().map(|bar|AnalysisBar{index:bar.index,time:bar.time,open:bar.open,high:bar.high,
        low:bar.low,close:bar.close,volume:Decimal::from(1000),adjustment_factor:Decimal::ONE,
        raw_trading_active:None,raw_is_st:None}).collect();
    let mut analyzer=TrendAnalyzer::new(AnalysisRequest{identity:DataIdentity{symbol:"sz.002724".into(),source_version:"fixture".into(),
        adjustment:"raw".into(),timeframe:"1w".into()},bars:input,config:AnalyzerConfig::default(),state:None}).unwrap();
    for _ in 0..5 {analyzer.process_next().unwrap();}
    let before=analyzer.levels().clone();let index=analyzer.get_current_index();
    let polyline=analyzer.execute_polyline_function(index).unwrap();
    assert_eq!(polyline.data.level,0);
    assert_eq!(polyline.next_offset,array_origin(&polyline.data));
    assert_eq!(analyzer.get_current_index(),index);
    for level in 1..=3{assert_eq!(analyzer.levels()[level],before[level]);}
    let first=analyzer.execute_level_from(1,index,&polyline.data,polyline.next_offset).unwrap();
    assert_eq!(first.data.level,1);
    assert_eq!(first.next_offset,array_origin(&first.data));
    assert_eq!(analyzer.levels()[2],before[2]);assert_eq!(analyzer.levels()[3],before[3]);
    assert!(analyzer.execute_level_from(3,index,&polyline.data,polyline.next_offset).is_err());
}

fn array_origin(frame:&kernel::KernelFrame)->usize{
    frame.preceding_start.as_ref().map_or(0,|origin|frame.points.iter().position(|point|
        point.order()==origin.order()&&point.kind==origin.kind&&point.value==origin.value).unwrap())
}
