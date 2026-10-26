//! 显式编译开关的线程内嵌套计时；含子调用时间与自身时间分开累计。
use std::{cell::RefCell,collections::BTreeMap,time::Instant};
use serde::Serialize;
#[derive(Default,Serialize)]
pub struct Measurement { pub calls:u64, pub inclusive_ms:f64, pub self_ms:f64 }
struct Entry {name:&'static str,start:Instant,children:f64}
#[derive(Default)]
struct State {stack:Vec<Entry>,rows:BTreeMap<String,Measurement>}
thread_local! {static STATE:RefCell<State>=RefCell::new(State::default());}
pub struct Guard;
pub fn enter(name:&'static str)->Guard {
    STATE.with(|state|state.borrow_mut().stack.push(Entry{name,start:Instant::now(),children:0.0}));Guard
}
impl Drop for Guard {fn drop(&mut self){
    STATE.with(|state|{let mut state=state.borrow_mut();let entry=state.stack.pop().expect("profile scope");
        let elapsed=entry.start.elapsed().as_secs_f64()*1000.0;
        let path=state.stack.iter().map(|s|s.name).chain(std::iter::once(entry.name)).collect::<Vec<_>>().join(" > ");
        if let Some(parent)=state.stack.last_mut(){parent.children+=elapsed;}
        let row=state.rows.entry(path).or_default();row.calls+=1;row.inclusive_ms+=elapsed;row.self_ms+=(elapsed-entry.children).max(0.0);
    });
}}
pub fn reset(){STATE.with(|state|{let mut state=state.borrow_mut();assert!(state.stack.is_empty());*state=State::default();});}
pub fn take()->BTreeMap<String,Measurement>{STATE.with(|state|{let mut state=state.borrow_mut();assert!(state.stack.is_empty());std::mem::take(&mut state.rows)})}
