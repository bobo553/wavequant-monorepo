use std::collections::BTreeMap;
use rust_decimal::Decimal;
use wavequant_quant_rs::indexed_json::{self as json,Value,wire};
use wavequant_quant_rs::analysis::kernel::{KernelPoint,KernelPointData,PointKind,PointState};

#[test]
fn evidence_versions_share_unchanged_children_and_never_rewrite_prior_state(){
    let child=json::json!({"origin":12,"known":15});let initial=json::json!({"left":child,"right":child});
    assert_eq!(initial["left"].id(),initial["right"].id());
    let mut next=initial.clone();assert_eq!(initial.id(),next.id());next["left"]["known"]=json::json!(16);
    assert_ne!(initial.id(),next.id());assert_eq!(initial["left"]["known"],15);assert_eq!(next["right"]["known"],15);
    assert_eq!(initial["right"].id(),next["right"].id());
}
#[test]
fn point_history_retains_reference_identity_until_a_new_version_is_written(){
    let original=KernelPoint::new(KernelPointData{index:3,ordinal:1,time:"2020-01-04".into(),kind:PointKind::H,value:12.0,
        known_index:5,level:1,path:0,state:PointState::Reversal,evidence:json::json!({"proof":{"known":5}})});
    let mut latest=original.clone();assert_eq!(original.reference_id(),latest.reference_id());latest.known_index=6;
    assert_ne!(original.reference_id(),latest.reference_id());assert_eq!(original.known_index,5);
    assert_eq!(original.evidence.id(),latest.evidence.id());
    let document=wire::to_vec(&vec![original.clone(),original]).unwrap();let decoded:Vec<KernelPoint>=wire::from_slice(&document).unwrap();
    assert_eq!(decoded[0].reference_id(),decoded[1].reference_id());
}
#[test]
fn transport_and_cache_roundtrip_reuse_the_same_node_for_repeated_proofs(){
    let proof=json::json!({"index":22,"value":10.25});let value=json::json!({"key":proof,"anchor":proof,"history":[proof,proof]});
    let encoded=wire::to_vec(&value).unwrap();let decoded:Value=wire::from_slice(&encoded).unwrap();assert_eq!(decoded,value);
    assert_eq!(decoded["key"].id(),decoded["anchor"].id());assert_eq!(decoded["key"].id(),decoded["history"][0].id());
}
#[test]
fn numeric_map_keys_enum_and_exact_decimal_keep_their_original_serde_contract(){
    #[derive(Debug,PartialEq,serde::Serialize,serde::Deserialize)]enum Mode{Complete{amount:Decimal},Waiting}
    let original=BTreeMap::from([(2usize,Mode::Complete{amount:"123456.123456789".parse().unwrap()}),(3,Mode::Waiting)]);
    let value=json::to_value(&original).unwrap();assert_eq!(json::to_string(&value).unwrap(),serde_json::to_string(&original).unwrap());
    assert_eq!(json::from_value::<BTreeMap<usize,Mode>>(value).unwrap(),original);
}
#[test]
fn transport_rejects_self_references_forward_references_and_invalid_roots(){
    for value in [json::json!({"$format":wire::FORMAT,"root":0,"nodes":[[4,[0]]]}),
        json::json!({"$format":wire::FORMAT,"root":0,"nodes":[[4,[1]],[0,null]]}),
        json::json!({"$format":wire::FORMAT,"root":7,"nodes":[[0,null]]})]{assert!(wire::unpack(value).is_err());}
}
