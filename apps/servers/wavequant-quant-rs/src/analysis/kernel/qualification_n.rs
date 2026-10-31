//! 资格通道的折叠 N：仅来源变化时发现结构，已发现候选按行情索引推进。
//! 与图表/ABC 的首 N 所有权分离；资格重开不能被上一窗口的首 N 占用。
use std::collections::BTreeSet;
use rust_decimal::Decimal;
use super::{KernelPoint,PointKind};
use crate::analysis::trading::{self,NSetup,NOptions,PivotRef,Direction,BoxAnchorMode,MilestoneBasis,NStatus,validated_bars::ValidatedBarSlice,
    single_n::{SingleNState,SingleNData,NPointRef,SqueezeState,advance_n_indexed,advance_squeeze_indexed}};
type Identity=[(usize,usize,usize);3];
#[derive(Clone)]
struct Candidate{identity:Identity,state:SingleNState,squeeze:SqueezeState}
#[derive(Clone,Default)]
pub(crate) struct FoldedNs{source_len:usize,seen:BTreeSet<Identity>,candidates:Vec<Candidate>}
impl FoldedNs{
    pub(crate) fn advance(&mut self,points:&[KernelPoint],prices:&[Decimal],validated:ValidatedBarSlice<'_>,now:usize,start:usize,window:usize,ttl:usize,shadow:Decimal)->Result<(),String>{
        let bars=validated.as_slice();
        self.candidates.retain(|item|item.state.candidate.as_ref().is_some_and(|n|now.saturating_sub(n.a.bar_index)<=window&&now<=n.known_index.saturating_add(ttl)));
        for item in &mut self.candidates{
            let step=advance_n_indexed(&mut item.state,points,prices,bars,now)?;
            if step.invalidated{continue;}
            if let Some(n)=&mut item.state.candidate{if now>0{advance_squeeze_indexed(&mut item.squeeze,n,bars,now,shadow)?;}}
        }
        self.candidates.retain(|item|item.state.candidate.is_some());
        if self.source_len==points.len(){return Ok(());}
        self.source_len=points.len();
        let first=(start+1).max(now.saturating_sub(window));
        self.seen.retain(|key|key[0].0>=first);
        // 相邻三点保留同根K线点序；折叠算法本身只产生跨日组合。
        let source=&points[points.partition_point(|p|p.index<first)..];
        let mut candidates:Vec<[KernelPoint;3]>=source.windows(3)
            .filter(|v|[v[0].kind,v[1].kind,v[2].kind]==[PointKind::L,PointKind::H,PointKind::L])
            .map(|v|[v[0].clone(),v[1].clone(),v[2].clone()]).collect();
        candidates.extend(trading::targets::folded_n_candidates(source,first,Direction::Up,true));
        for vertices in candidates{
            let identity=vertices.each_ref().map(|p|(p.index,p.ordinal,p.known_index));
            if !self.seen.insert(identity){continue;}
            let [a,b,c]=&vertices;let known=a.known_index.max(b.known_index).max(c.known_index);
            let mother=a.index==b.index&&b.index<c.index&&a.ordinal<b.ordinal;
            let same=a.index<b.index&&b.index==c.index&&b.ordinal<c.ordinal;
            if known>now||now>known.saturating_add(ttl)||!(a.index<b.index&&b.index<c.index||mother||same){continue;}
            let inside=same&&b.index>0&&trading::teaching_inside(&bars[b.index-1],&bars[b.index]);
            let setup=NSetup{symbol:bars[0].symbol.clone(),timeframe:"1d".into(),direction:Direction::Up,
                origin:PivotRef{index:a.index,confirmed_index:a.known_index,ordinal:None},neckline:PivotRef{index:b.index,confirmed_index:b.known_index,ordinal:None},
                pullback:PivotRef{index:c.index,confirmed_index:c.known_index,ordinal:None},source:"lecture_causal".into(),box_anchor_mode:BoxAnchorMode::AttackVirtualExtreme,
                allow_confirmation_bar:true,allow_outside_close:true,allow_mother_impulse:mother,staged_defense:true,allow_mother_pullback:same&&!inside,allow_inside_pullback:inside,polyline_structure:false};
            let Ok(observed)=trading::n_shape::observe_n_validated(validated,&setup,&NOptions{timeframe:"1d".into(),milestone_basis:MilestoneBasis::Extreme,asof_index:Some(now),measurement_start:None,measure_targets:false})else{continue;};
            if matches!(observed.status,NStatus::Invalidated|NStatus::PullbackExtended){continue;}
            let Some(anchors)=observed.anchors else{continue;};
            let reference=|p:&KernelPoint|->Result<NPointRef,String>{let source_index=points.binary_search_by_key(&p.order(),KernelPoint::order).map_err(|_|"folded N source disappeared")?;
                Ok(NPointRef{path:p.path,source_index,bar_index:p.index,ordinal:p.ordinal,known_index:p.known_index})};
            let completion=observed.completion;let attack=completion.as_ref().map(|x|x.bar_index);
            if completion.as_ref().is_some_and(|c|bars[c.bar_index..=now].iter().any(|bar|bar.low<c.defense)){continue;}
            let mut data=SingleNData{direction:Direction::Up,a:reference(a)?,b:reference(b)?,c:reference(c)?,origin:anchors.origin,neckline:anchors.neckline_extreme,
                neckline_close:anchors.neckline_close,pullback:anchors.pullback,known_index:known,attack_index:attack,defense:completion.as_ref().map(|x|x.defense),
                box_anchor:completion.as_ref().map(|x|x.virtual_high),squeeze_index:None,regime:None,targets:None};
            let mut squeeze=SqueezeState::default();
            if let Some(attack)=attack{for day in attack..=now{if day>0{advance_squeeze_indexed(&mut squeeze,&mut data,bars,day,shadow)?;}}}
            if data.defense.is_some_and(|defense|bars[now].low<defense){continue;}
            self.candidates.push(Candidate{identity,state:SingleNState::from_candidate(data,points,now),squeeze});
        }
        self.candidates.sort_by_key(|item|item.identity);Ok(())
    }
    pub(crate) fn values(&self)->impl Iterator<Item=&SingleNData>{self.candidates.iter().filter_map(|item|item.state.candidate.as_ref())}
}
