//! 上级严格突破的索引回执；只有回执变化才生成新的下级来源版本。
use std::collections::BTreeMap;
use super::{Direction, KernelBar, KernelPoint, KernelPointData, PromotionReceipt};
use super::arena::{Fact, PointArena, PointId, RefFrame};

#[derive(Clone, Copy)]
pub(super) struct Receipt {
    upper: u8, target: u8, known: usize,
    origin: PointId, attack: PointId, key: PointId, direction: Direction,
}
type PointKey = (usize, usize, u8, u64, usize);
type ReceiptKey = (u8, u8, usize, PointKey, PointKey, PointKey);
#[derive(Default)]
pub(super) struct ReceiptStore {
    records: Vec<Receipt>, identities: BTreeMap<ReceiptKey, usize>,
    aliases: BTreeMap<(PointId, Vec<usize>), PointId>, attached: BTreeMap<PointId, Vec<usize>>,
    formal_aliases: BTreeMap<(PointId,usize),PointId>,
}
fn key(pool: &PointArena, bars: &[KernelBar], id: PointId) -> PointKey {
    let p = pool.view(id, bars);
    (p.index, p.ordinal, p.kind as u8, p.value.to_bits(), p.known_index)
}
pub(crate) fn collect(pool: &mut PointArena, bars: &[KernelBar], frame: &RefFrame, target: u8) -> Result<bool, String> {
    let mut changed = false;
    for proof in &frame.promotions {
        let facts = &pool.proofs[*proof];
        let known = facts.iter().find_map(|(k,f)| if *k == "available_index" { if let Fact::Number(n)=f {Some(*n)} else {None} } else {None}).ok_or("missing native promotion clock")?;
        let point = |name| facts.iter().find_map(|(k, f)| if *k == name { if let Fact::Point(id) = f { Some(*id) } else { None } } else { None });
        let (Some(origin), Some(attack), Some(boundary)) = (point("origin"), point("confirmed_by"), point("broken_key")) else { return Err("missing native promotion references".into()); };
        let direction = facts.iter().find_map(|(k, f)| if *k == "direction" { if let Fact::Direction(d) = f { Some(*d) } else { None } } else { None }).ok_or("missing native promotion direction")?;
        let (a, b, k) = (pool.view(origin, bars), pool.view(attack, bars), pool.view(boundary, bars));
        if target == 0 || frame.level <= target || k.order() >= b.order() || a.order() >= b.order() || k.known_index >= b.index
            || direction.target() != b.kind || a.kind == b.kind || k.kind != b.kind
            || direction.sign() * (b.value - a.value) <= 0.0 || direction.sign() * (b.value - k.value) <= 0.0
            || [a.known_index, b.known_index, k.known_index].iter().any(|n| *n > known) {
            return Err("invalid native causal promotion".into());
        }
        let identity = (frame.level, target, known, key(pool,bars,origin), key(pool,bars,attack), key(pool,bars,boundary));
        if !pool.promotions.identities.contains_key(&identity) {
            let index = pool.promotions.records.len();
            pool.promotions.records.push(Receipt { upper:frame.level, target, known, origin, attack, key:boundary, direction });
            pool.promotions.identities.insert(identity,index); changed = true;
        }
    }
    Ok(changed)
}
pub(crate) fn source(pool: &mut PointArena, bars: &[KernelBar], points: &[PointId], level: u8) -> Vec<PointId> {
    let positions: BTreeMap<_,_> = points.iter().enumerate().map(|(i,id)|(pool.view(*id,bars).order(),i)).collect();
    let mut attachments: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (index, receipt) in pool.promotions.records.iter().enumerate().filter(|(_,r)| r.target == level) {
        let find = |reference| positions.get(&pool.view(reference,bars).order()).copied().filter(|i| pool.same(points[*i],reference,bars) && pool.view(points[*i],bars).known_index <= receipt.known);
        if let (Some(_), Some(endpoint)) = (find(receipt.origin), find(receipt.attack)) { attachments.entry(endpoint).or_default().push(index); }
    }
    let mut result = points.to_vec();
    for (position, receipts) in attachments {
        let original = points[position]; let identity = (original, receipts.clone());
        let alias = if let Some(id) = pool.promotions.aliases.get(&identity) { *id } else {
            let id = pool.push(pool.nodes[original.0]);
            pool.promotions.attached.insert(id,receipts); pool.promotions.aliases.insert(identity,id); id
        };
        result[position] = alias;
    }
    result
}
pub(super) fn attached(pool: &PointArena, bars: &[KernelBar], id: PointId) -> Vec<PromotionReceipt> {
    let scalar = |id| { let p = pool.view(id,bars); KernelPoint::new(KernelPointData { index:p.index,ordinal:p.ordinal,kind:p.kind,value:p.value,
        known_index:p.known_index,level:p.level,path:p.path,state:p.state,time:pool.time(id,bars).into(),evidence:crate::indexed_json::Value::Null }) };
    pool.promotions.attached.get(&id).into_iter().flatten().map(|index| {
        let r = pool.promotions.records[*index]; PromotionReceipt { upper_level:r.upper,target_level:r.target,path:pool.view(id,bars).path,
            known_index:r.known,origin:scalar(r.origin),attack:scalar(r.attack),key:scalar(r.key),direction:r.direction }
    }).collect()
}

/// 严格端点用自身证明时钟；普通点仍用真实结束时钟。发展尾端没有证明时不进入。
pub(crate) fn formal_source(pool:&mut PointArena,bars:&[KernelBar],frame:&RefFrame)->Vec<PointId>{
    let mut earliest:BTreeMap<(usize,usize),(usize,usize)>=BTreeMap::new();
    for proof in &frame.promotions {
        let facts=&pool.proofs[*proof];
        let attack=facts.iter().find_map(|(k,f)|if *k=="confirmed_by"{if let Fact::Point(id)=f{Some(*id)}else{None}}else{None});
        let known=facts.iter().find_map(|(k,f)|if *k=="available_index"{if let Fact::Number(n)=f{Some(*n)}else{None}}else{None});
        if let (Some(attack),Some(known))=(attack,known){
            let order=pool.view(attack,bars).order();
            if earliest.get(&order).is_none_or(|(old,_)|known<*old){earliest.insert(order,(known,*proof));}
        }
    }
    frame.points.iter().chain(frame.candidate.iter()).filter_map(|id|{
        let point=pool.view(*id,bars);
        let Some(&(known,proof))=earliest.get(&point.order())else{return point.is_closed().then_some(*id);};
        if point.is_closed()&&known>=point.known_index{return Some(*id);}
        if let Some(alias)=pool.promotions.formal_aliases.get(&(*id,proof)){return Some(*alias);}
        let mut node=pool.nodes[id.0];node.known_index=known;node.state=super::PointState::Reversal;
        node.strict_breaks=Some(pool.append_strict(node.strict_breaks,proof));
        let alias=pool.push(node);pool.promotions.formal_aliases.insert((*id,proof),alias);Some(alias)
    }).collect()
}
