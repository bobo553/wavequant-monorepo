use wavequant_quant_rs::analysis::kernel::{self,Direction,KernelBar,PointKind,PolylineCursor};

fn bars(rows:&[(f64,f64,f64,f64)])->Vec<KernelBar>{
    rows.iter().copied().enumerate().map(|(index,(open,high,low,close))|KernelBar{
        index,time:format!("2020-01-{:02}",index+1),open,high,low,close,
    }).collect()
}

#[test]
fn a_bearish_mother_below_the_child_connects_the_existing_high_directly_to_its_low(){
    let input=bars(&[(8.0,9.0,7.0,8.5),(9.3,9.5,9.0,9.4),(8.9,9.8,8.7,8.8)]);
    let frame=kernel::polyline(&input).unwrap();
    assert_eq!(frame.direction,Some(Direction::Down));
    assert_eq!(frame.points.len(),1);
    let high=&frame.points[0];assert_eq!((high.index,high.kind,high.value,high.known_index),(1,PointKind::H,9.5,2));
    let low=frame.candidate.as_ref().unwrap();assert_eq!((low.index,low.kind,low.value,low.known_index),(2,PointKind::L,8.7,2));
    assert!(frame.issues.is_empty());
}

#[test]
fn a_falling_leg_extends_to_the_mother_low_without_adding_the_mother_high(){
    let input=bars(&[(11.0,12.0,10.0,11.5),(9.3,9.5,9.0,9.4),(8.9,9.8,8.7,8.8)]);
    let frame=kernel::polyline(&input).unwrap();
    assert_eq!(frame.direction,Some(Direction::Down));
    assert!(!frame.points.iter().any(|point|point.index==2&&point.kind==PointKind::H));
    let low=frame.candidate.as_ref().unwrap();assert_eq!((low.index,low.kind,low.value),(2,PointKind::L,8.7));
}

#[test]
fn the_first_pair_uses_only_the_child_high_and_mother_low_for_any_child_colour(){
    for(open,close)in [(9.3,9.4),(9.4,9.3),(9.3,9.3)]{
        let input=bars(&[(open,9.5,9.0,close),(8.9,9.8,8.7,8.8)]);
        let frame=kernel::polyline(&input).unwrap();
        assert!(frame.issues.is_empty());assert_eq!(frame.points.len(),1);
        assert_eq!((frame.points[0].index,frame.points[0].kind,frame.points[0].value),(0,PointKind::H,9.5));
        let low=frame.candidate.as_ref().unwrap();assert_eq!((low.index,low.kind,low.value),(1,PointKind::L,8.7));
    }
}

#[test]
fn shared_extremes_touching_or_overlapping_bodies_and_a_bullish_mother_keep_the_old_path(){
    for mother in [(8.9,9.5,8.7,8.8),(9.2,9.8,9.0,9.1),(9.3,9.8,8.7,8.8),
        (9.35,9.8,8.7,8.8),(8.8,9.8,8.7,8.9)]{
        let input=bars(&[(9.3,9.5,9.0,9.4),mother]);
        let frame=kernel::polyline(&input).unwrap();
        assert!(frame.points.iter().chain(frame.candidate.iter()).any(|point|point.index==1&&point.kind==PointKind::H));
    }
    let frame=kernel::polyline(&bars(&[(9.3,9.5,9.0,9.4),(8.9,9.8,8.7,8.9)])).unwrap();
    assert!(!frame.issues.is_empty());
}

#[test]
fn append_and_cold_results_agree_without_backfilling_a_mother_high_on_the_next_child(){
    let input=bars(&[(8.0,9.0,7.0,8.5),(9.3,9.5,9.0,9.4),(8.9,9.8,8.7,8.8),
        (9.0,9.2,8.8,9.1),(9.2,9.6,9.0,9.4)]);
    let mut cursor=PolylineCursor::default();
    for end in 1..=input.len(){
        let frame=cursor.advance(&input[..end]).unwrap();
        assert_eq!(frame,kernel::polyline(&input[..end]).unwrap());
        assert_eq!(cursor.advance(&input[..end]).unwrap(),frame);
        if end>=3{assert!(!frame.points.iter().chain(frame.candidate.iter()).any(|point|point.index==2&&point.kind==PointKind::H));}
        assert!(frame.points.iter().chain(frame.candidate.iter()).all(|point|point.known_index<end));
    }
}
