//! 传输和持久化共用节点表。引用只指向更早的节点，拒绝循环及越界输入。
use super::{Value,Kind,Map,Result,error};
use std::collections::{BTreeMap,HashMap};
use serde::Serialize;
pub const FORMAT:&str="wavequant.indexed.v1";
const MAX_NODES:usize=1_000_000;
const MAX_DEPTH:usize=256;
#[derive(PartialEq,Eq,PartialOrd,Ord)]
enum Key{Null,Bool(bool),Number(String),String(String),Array(Vec<usize>),Object(Vec<(String,usize)>)}
struct Builder{seen:HashMap<u64,usize>,intern:BTreeMap<Key,usize>,nodes:Vec<Value>}
impl Builder{
    fn visit(&mut self,v:&Value,depth:usize)->Result<usize>{
        if depth>MAX_DEPTH{return Err(error("indexed document depth exceeds bound"));}
        if let Some(id)=self.seen.get(&v.id()){return Ok(*id);}
        let(key,kind,payload)=match v.kind(){
            None=>(Key::Null,0,Value::Null),Some(Kind::Bool(b))=>(Key::Bool(*b),1,v.clone()),
            Some(Kind::Number(n))=>(Key::Number(n.to_string()),2,v.clone()),Some(Kind::String(s))=>(Key::String(s.clone()),3,v.clone()),
            Some(Kind::Array(items))=>{let ids=items.iter().map(|v|self.visit(v,depth+1)).collect::<Result<Vec<_>>>()?;
                let payload=super::to_value(&ids)?;(Key::Array(ids),4,payload)},
            Some(Kind::Object(fields))=>{let ids=fields.iter().map(|(k,v)|Ok((k.clone(),self.visit(v,depth+1)?))).collect::<Result<Vec<_>>>()?;
                let payload=super::to_value(&ids)?;(Key::Object(ids),5,payload)},
        };
        if let Some(id)=self.intern.get(&key){self.seen.insert(v.id(),*id);return Ok(*id);}
        if self.nodes.len()>=MAX_NODES{return Err(error("indexed document node budget exceeded"));}
        let id=self.nodes.len();self.nodes.push(Value::Array(vec![super::to_value(&kind)?,payload]));self.intern.insert(key,id);self.seen.insert(v.id(),id);Ok(id)
    }
}
pub fn pack(v:&Value)->Result<Value>{
    let mut b=Builder{seen:HashMap::new(),intern:BTreeMap::new(),nodes:Vec::new()};let root=b.visit(v,0)?;
    Ok(Value::Object(Map::from([("$format".into(),Value::String(FORMAT.into())),("root".into(),super::to_value(&root)?),("nodes".into(),Value::Array(b.nodes))])))
}
pub fn is_document(v:&Value)->bool{v.get("$format").and_then(Value::as_str)==Some(FORMAT)}
pub fn unpack(v:Value)->Result<Value>{
    if !is_document(&v){return Ok(v);}
    let rows=v["nodes"].as_array().filter(|v|v.len()<=MAX_NODES).ok_or_else(||error("invalid indexed node table"))?;
    let mut nodes:Vec<Value>=Vec::with_capacity(rows.len());let mut depths=Vec::with_capacity(rows.len());
    let reference=|v:&Value,nodes:&Vec<Value>,depths:&Vec<usize>|->Result<(Value,usize)>{
        let id=v.as_u64().and_then(|id|usize::try_from(id).ok()).ok_or_else(||error("invalid indexed node reference"))?;
        Ok((nodes.get(id).cloned().ok_or_else(||error("indexed reference must precede its owner"))?,*depths.get(id).ok_or_else(||error("missing indexed depth"))?))
    };
    for row in rows{
        let row=row.as_array().filter(|v|v.len()==2).ok_or_else(||error("invalid indexed node"))?;let mut depth=0;
        let node=match row[0].as_u64(){
            Some(0) if row[1].is_null()=>Value::Null,Some(1) if row[1].is_boolean()=>row[1].clone(),Some(2) if row[1].is_number()=>row[1].clone(),Some(3) if row[1].is_string()=>row[1].clone(),
            Some(4)=>{let children=row[1].as_array().ok_or_else(||error("invalid indexed array"))?;let mut out=Vec::with_capacity(children.len());
                for id in children{let(v,d)=reference(id,&nodes,&depths)?;depth=depth.max(d+1);out.push(v);}Value::Array(out)},
            Some(5)=>{let children=row[1].as_array().ok_or_else(||error("invalid indexed object"))?;let mut out=Map::new();
                for pair in children{let pair=pair.as_array().filter(|v|v.len()==2).ok_or_else(||error("invalid indexed field"))?;
                    let key=pair[0].as_str().ok_or_else(||error("indexed field name required"))?;let(v,d)=reference(&pair[1],&nodes,&depths)?;
                    depth=depth.max(d+1);if out.insert(key.into(),v).is_some(){return Err(error("duplicate indexed field"));}}
                Value::Object(out)},
            _=>return Err(error("invalid indexed node kind")),
        };
        if depth>MAX_DEPTH{return Err(error("indexed document depth exceeds bound"));}nodes.push(node);depths.push(depth);
    }
    let id=v["root"].as_u64().and_then(|id|usize::try_from(id).ok()).ok_or_else(||error("invalid indexed root"))?;
    nodes.get(id).cloned().ok_or_else(||error("indexed root out of range"))
}
pub fn to_vec<T:Serialize+?Sized>(v:&T)->Result<Vec<u8>>{super::to_vec(&pack(&super::to_value(v)?)?)}
pub fn from_slice<T:serde::de::DeserializeOwned>(v:&[u8])->Result<T>{super::from_value(unpack(super::from_slice(v)?)?)}
