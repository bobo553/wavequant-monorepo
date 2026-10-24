use wavequant_quant_rs::analysis::kernel::{self,KernelPoint,KernelPointData,KernelOptions,PointKind,PointState,TrendCursor};
fn points(level:u8)->Vec<KernelPoint>{
    [8.0,12.0,10.0,15.0,11.0,17.0,9.0,13.0,7.0,11.0,6.0,14.0,10.0,18.0].iter().enumerate().map(|(i,v)|KernelPoint::new(KernelPointData{
        index:i*3+5,ordinal:0,time:format!("2020-01-{:02}",i+1),kind:if i%2==0{PointKind::L}else{PointKind::H},value:*v,known_index:i*3+5,level:level-1,path:0,state:PointState::Confirmed,evidence:wavequant_quant_rs::indexed_json::Value::Null})).collect()
}
#[test]
fn offset_and_disabled_full_scan_agree_for_append_tail_revision_and_early_revision(){
    for level in 1..=3{
        let mut input=points(level);let mut cursor=TrendCursor::default();let options=KernelOptions::default();let mut full=options.clone();full.trend_offset_enabled=false;
        for end in 5..=input.len(){let asof=input[end-1].known_index;let offset=4.min(end);
            assert_eq!(cursor.advance_from(&input[..end],offset,level,asof,&options).unwrap(),TrendCursor::default().advance_from(&input[..end],offset,level,asof,&full).unwrap());}
        input.last_mut().unwrap().value+=0.25;let asof=input.last().unwrap().known_index;
        let before=cursor.statistics().scanned_points;
        assert_eq!(cursor.advance_from(&input,4,level,asof,&options).unwrap(),kernel::trend(&input,level,asof,&full).unwrap());
        assert!(cursor.statistics().checkpoint_resumes>0);
        assert!(cursor.statistics().scanned_points-before<input.len());
        input[2].value-=0.125;
        assert_eq!(cursor.advance_from(&input,4,level,asof,&options).unwrap(),kernel::trend(&input,level,asof,&full).unwrap());
        assert_eq!(cursor.advance_from(&input[..8],1,level,input[7].known_index,&options).unwrap(),kernel::trend(&input[..8],level,input[7].known_index,&full).unwrap());
        assert!(cursor.advance_from(&input,input.len()+1,level,asof,&options).is_err());
    }
}
#[test]
fn disabling_offset_rebuilds_and_toggle_drops_previous_checkpoints(){
    let input=points(1);let asof=input.last().unwrap().known_index;let mut cursor=TrendCursor::default();let mut options=KernelOptions::default();
    let first=cursor.advance_from(&input,4,1,asof,&options).unwrap();
    options.trend_offset_enabled=false;let before=cursor.statistics().scanned_points;
    assert_eq!(cursor.advance_from(&input,4,1,asof,&options).unwrap(),first);
    assert_eq!(cursor.statistics().scanned_points-before,input.len());
    options.trend_offset_enabled=true;
    assert_eq!(cursor.advance_from(&input,input.len(),1,asof,&options).unwrap(),first);
    assert_eq!(cursor.advance_from(&input,0,1,asof,&options).unwrap(),first);
}
