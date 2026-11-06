use super::*;

#[test]
fn late_subscription_recovers_attack_and_keeps_n_across_defense_wick(){
    for direction in [Direction::Down,Direction::Up]{
        let mut source=Source{points:vec![point(0,0,1,PointKind::H,20),point(1,0,2,PointKind::L,15),point(2,0,3,PointKind::H,18)],prices:vec![20.into(),15.into(),18.into()]};
        let mut bars=vec![bar(0,19,20,18,19),bar(1,18,18,15,16),bar(2,17,18,16,17),bar(3,17,18,14,14),
            bar(4,15,17,13,15),bar(5,15,17,14,16),bar(6,17,19,16,17),bar(7,17,19,16,19)];
        if direction==Direction::Up{mirrored(&mut source,&mut bars);}
        let mut search=SingleNState::from_origin(0,0,direction);
        single_n::advance_n_indexed(&mut search,&source.points,&source.prices,&bars,5).unwrap();
        assert_eq!(search.candidate.as_ref().unwrap().attack_index,None);
        let mut store=NStore::default();let id=store.intern(search).unwrap();store.link(id,(0,0),5);
        store.advance(id,&source,&bars,5,&TradingDayConfig::lecture()).unwrap();
        assert_eq!(store.data(id).attack_index,Some(3));
        assert_eq!(store.entries[id.0].target_history[0].known_index,3);
        store.advance(id,&source,&bars,6,&TradingDayConfig::lecture()).unwrap();
        assert!(store.valid(id));assert_eq!(store.data(id).attack_index,Some(3));
        store.advance(id,&source,&bars,7,&TradingDayConfig::lecture()).unwrap();
        assert!(!store.valid(id));assert_eq!(store.entries[id.0].invalidated,Some(7));
    }
}

fn bar(day:usize,open:i64,high:i64,low:i64,close:i64)->RuleCandle{
    RuleCandle{symbol:"fixture".into(),timestamp:format!("2026-01-{:02}",day+1),open:open.into(),high:high.into(),low:low.into(),close:close.into(),volume:100.into(),adjustment_factor:Decimal::ONE,raw_is_st:None,raw_trading_active:None}
}
fn point(index:usize,ordinal:usize,known:usize,kind:PointKind,value:i64)->KernelPoint{
    KernelPoint::new(super::super::KernelPointData{index,ordinal,known_index:known,kind,value:value as f64,level:0,path:0,state:super::super::PointState::Confirmed,time:format!("2026-01-{:02}",index+1),evidence:crate::indexed_json::Value::Null})
}
fn mirrored(source:&mut Source,bars:&mut[RuleCandle]){
    for p in &mut source.points{p.kind=if p.kind==PointKind::H{PointKind::L}else{PointKind::H};p.value=100.0-p.value;}
    for price in &mut source.prices{*price=Decimal::from(100)-*price;}
    for b in bars{let high=b.high;b.open=Decimal::from(100)-b.open;b.high=Decimal::from(100)-b.low;b.low=Decimal::from(100)-high;b.close=Decimal::from(100)-b.close;}
}
fn nested_fixture(direction:Direction)->(Source,Vec<RuleCandle>){
    let mut source=Source{points:vec![point(0,0,0,PointKind::L,10),point(1,0,1,PointKind::H,20),point(2,0,2,PointKind::L,12),point(3,0,3,PointKind::H,16),point(4,0,4,PointKind::L,14)],prices:vec![10.into(),20.into(),12.into(),16.into(),14.into()]};
    let mut bars=vec![bar(0,11,12,10,11),bar(1,15,20,14,19),bar(2,13,14,12,13),bar(3,14,16,13,15),bar(4,15,15,14,14),bar(5,15,18,15,17)];
    if direction==Direction::Down{mirrored(&mut source,&mut bars);}(source,bars)
}
#[test]
fn later_local_n_attacks_before_pending_outer_n_in_both_directions(){
    for direction in [Direction::Up,Direction::Down]{
        let(source,bars)=nested_fixture(direction);let mut search=SingleNState::from_origin(0,0,direction);
        single_n::advance_n_indexed(&mut search,&source.points,&source.prices,&bars,2).unwrap();
        let mut store=NStore::default();let old=store.intern(search).unwrap();store.link(old,(1,0),2);store.link(old,(2,0),2);
        for now in 2..=5{store.advance(old,&source,&bars,now,&TradingDayConfig::lecture()).unwrap();}
        let next=store.entries[old.0].replacement.unwrap();
        // intern只登记新索引；按正式接管路径推进后才发布恢复的攻击时钟。
        store.advance(next,&source,&bars,5,&TradingDayConfig::lecture()).unwrap();let n=store.data(next);
        assert_eq!([n.a.bar_index,n.b.bar_index,n.c.bar_index],[2,3,4]);assert_eq!(n.attack_index,Some(5));
        assert_eq!(store.entries[old.0].invalidated,Some(5));assert_eq!(store.entries.len(),2);
        for level in [1,2]{
            let mut tracker=Tracker{origin:MemoryPoint::from_point(&source.points[0]),direction,search:None,current:Some(old),retained:None};
            tracker.release_invalid(&source,&bars,5,(level,0),&mut store);assert_eq!(tracker.current,Some(next));
        }
        assert_eq!(store.entries[next.0].owners.len(),2);
    }
}
#[test]
fn attacked_n_with_intact_defense_does_not_search_for_a_later_n(){
    for direction in [Direction::Up,Direction::Down]{
        let(source,bars)=nested_fixture(direction);let mut search=SingleNState::from_origin(0,0,direction);
        single_n::advance_n_indexed(&mut search,&source.points,&source.prices,&bars,2).unwrap();
        let n=search.candidate.as_mut().unwrap();n.attack_index=Some(2);n.defense=Some(if direction==Direction::Up{11.into()}else{89.into()});n.box_anchor=Some(if direction==Direction::Up{21.into()}else{79.into()});
        let mut store=NStore::default();let id=store.intern(search).unwrap();
        for now in 2..=5{store.advance(id,&source,&bars,now,&TradingDayConfig::lecture()).unwrap();}
        assert!(store.valid(id));assert!(store.entries[id.0].replacement.is_none());assert!(store.entries[id.0].pending_search.is_none());assert_eq!(store.entries.len(),1);
    }
}
#[test]
fn reversal_origin_and_extended_neckline_survive_until_same_day_attack(){
    for level in 0..4{for direction in [Direction::Down,Direction::Up]{
        let mut source=Source{points:vec![point(0,0,0,PointKind::L,48),point(2,0,2,PointKind::H,50),point(3,0,4,PointKind::L,40),point(3,1,4,PointKind::H,42),point(4,0,4,PointKind::L,39),point(4,1,5,PointKind::H,45),point(5,0,5,PointKind::L,41),point(6,0,6,PointKind::H,47)],prices:vec![48.into(),50.into(),40.into(),42.into(),39.into(),45.into(),41.into(),47.into()]};
        let mut bars=vec![bar(0,49,50,48,49),bar(1,49,50,48,49),bar(2,49,50,46,47),bar(3,41,42,40,41),bar(4,40,45,39,44),bar(5,43,44,41,43),bar(6,46,47,37,38)];
        if direction==Direction::Up{mirrored(&mut source,&mut bars);}
        let mut path=PathState::default();let mut store=NStore::default();
        path.observe_flip(MemoryPoint::from_point(&source.points[1]),MemoryPoint::from_point(&source.points[0]),MemoryPoint::from_point(&source.points[4]),direction,4,level,&mut store);
        for now in 4..=6{path.advance(&source,&bars,now,level,&mut store,&TradingDayConfig::lecture()).unwrap();}
        let id=path.tracker.as_ref().unwrap().current.unwrap();let n=store.data(id);
        assert_eq!([n.a.bar_index,n.b.bar_index,n.c.bar_index],[2,4,6]);assert_eq!(n.attack_index,Some(6));
    }}
}
