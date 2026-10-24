//! 共享证据节点：复制只传节点引用，更新只复制被修改容器的子下标。
mod macros;
mod ser;
mod de;
pub mod wire;
use std::{cell::{Cell,RefCell},collections::BTreeMap,fmt,ops::{Index,IndexMut},sync::{Arc,atomic::{AtomicU64,Ordering}}};
use serde::{Serialize,Deserialize};
pub use serde_json::{Error,Number,Deserializer,StreamDeserializer};
pub use crate::indexed_json_macro as json;
pub type Result<T> = std::result::Result<T,Error>;
pub type Map<K,V> = BTreeMap<K,V>;
const MARKER:&str="wavequant.indexed-value";
static NEXT:AtomicU64=AtomicU64::new(1);
#[derive(Clone,Debug,PartialEq)]
pub enum Kind {Bool(bool),Number(Number),String(String),Array(Vec<Value>),Object(Map<String,Value>)}
#[derive(Clone,Debug)]
struct Node {id:u64,kind:Kind}
#[derive(Clone,Default)]
pub struct Value(Option<Arc<Node>>);
impl fmt::Debug for Value{fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result{match &self.0{None=>f.write_str("Null"),Some(n)=>n.kind.fmt(f)}}}
impl PartialEq for Value{fn eq(&self,other:&Self)->bool{self.id()==other.id()||match(self.kind(),other.kind()){(Some(a),Some(b))=>a==b,_=>false}}}
#[allow(non_snake_case,non_upper_case_globals)]
impl Value {
    pub const Null:Self=Self(None);
    pub fn Bool(v:bool)->Self{Self::new(Kind::Bool(v))}
    pub fn Number(v:Number)->Self{Self::new(Kind::Number(v))}
    pub fn String(v:String)->Self{Self::new(Kind::String(v))}
    pub fn Array(v:Vec<Self>)->Self{Self::new(Kind::Array(v))}
    pub fn Object(v:Map<String,Self>)->Self{Self::new(Kind::Object(v))}
    fn new(kind:Kind)->Self{Self(Some(Arc::new(Node{id:NEXT.fetch_add(1,Ordering::Relaxed),kind})))}
    pub fn id(&self)->u64{self.0.as_ref().map_or(0,|n|n.id)}
    pub fn kind(&self)->Option<&Kind>{self.0.as_ref().map(|n|&n.kind)}
    pub fn kind_mut(&mut self)->Option<&mut Kind>{self.0.as_mut().map(|n|{let n=Arc::make_mut(n);n.id=NEXT.fetch_add(1,Ordering::Relaxed);&mut n.kind})}
    pub fn as_object(&self)->Option<&Map<String,Self>>{match self.kind(){Some(Kind::Object(v))=>Some(v),_=>None}}
    pub fn as_object_mut(&mut self)->Option<&mut Map<String,Self>>{match self.kind_mut(){Some(Kind::Object(v))=>Some(v),_=>None}}
    pub fn as_array(&self)->Option<&Vec<Self>>{match self.kind(){Some(Kind::Array(v))=>Some(v),_=>None}}
    pub fn as_array_mut(&mut self)->Option<&mut Vec<Self>>{match self.kind_mut(){Some(Kind::Array(v))=>Some(v),_=>None}}
    pub fn as_str(&self)->Option<&str>{match self.kind(){Some(Kind::String(v))=>Some(v),_=>None}}
    pub fn as_number(&self)->Option<&Number>{match self.kind(){Some(Kind::Number(v))=>Some(v),_=>None}}
    pub fn as_bool(&self)->Option<bool>{match self.kind(){Some(Kind::Bool(v))=>Some(*v),_=>None}}
    pub fn as_i64(&self)->Option<i64>{self.as_number()?.as_i64()}
    pub fn as_u64(&self)->Option<u64>{self.as_number()?.as_u64()}
    pub fn as_f64(&self)->Option<f64>{self.as_number()?.as_f64()}
    pub fn is_null(&self)->bool{self.0.is_none()}
    pub fn is_object(&self)->bool{self.as_object().is_some()}
    pub fn is_array(&self)->bool{self.as_array().is_some()}
    pub fn is_string(&self)->bool{self.as_str().is_some()}
    pub fn is_number(&self)->bool{self.as_number().is_some()}
    pub fn is_boolean(&self)->bool{self.as_bool().is_some()}
    pub fn is_i64(&self)->bool{self.as_number().is_some_and(Number::is_i64)}
    pub fn is_u64(&self)->bool{self.as_number().is_some_and(Number::is_u64)}
    pub fn is_f64(&self)->bool{self.as_number().is_some_and(Number::is_f64)}
    pub fn get<I:ValueIndex>(&self,index:I)->Option<&Self>{index.get(self)}
    pub fn get_mut<I:ValueIndex>(&mut self,index:I)->Option<&mut Self>{index.get_mut(self)}
    pub fn take(&mut self)->Self{std::mem::take(self)}
    pub fn pointer(&self,pointer:&str)->Option<&Self>{
        if pointer.is_empty(){return Some(self);}if !pointer.starts_with('/'){return None;}
        pointer[1..].split('/').try_fold(self,|v,part|{let key=part.replace("~1","/").replace("~0","~");if v.is_array(){v.get(key.parse::<usize>().ok()?)}else{v.get(key.as_str())}})
    }
}
pub trait ValueIndex {fn get<'a>(self,v:&'a Value)->Option<&'a Value>;fn get_mut<'a>(self,v:&'a mut Value)->Option<&'a mut Value>;fn insert<'a>(self,v:&'a mut Value)->&'a mut Value;}
impl ValueIndex for &str {
    fn get<'a>(self,v:&'a Value)->Option<&'a Value>{v.as_object()?.get(self)}
    fn get_mut<'a>(self,v:&'a mut Value)->Option<&'a mut Value>{v.as_object_mut()?.get_mut(self)}
    fn insert<'a>(self,v:&'a mut Value)->&'a mut Value{if v.is_null(){*v=Value::Object(Map::new());}v.as_object_mut().expect("JSON field requires object").entry(self.into()).or_default()}
}
impl ValueIndex for &String{fn get<'a>(self,v:&'a Value)->Option<&'a Value>{<&str as ValueIndex>::get(self.as_str(),v)}fn get_mut<'a>(self,v:&'a mut Value)->Option<&'a mut Value>{<&str as ValueIndex>::get_mut(self.as_str(),v)}fn insert<'a>(self,v:&'a mut Value)->&'a mut Value{<&str as ValueIndex>::insert(self.as_str(),v)}}
impl ValueIndex for &&str{fn get<'a>(self,v:&'a Value)->Option<&'a Value>{<&str as ValueIndex>::get(*self,v)}fn get_mut<'a>(self,v:&'a mut Value)->Option<&'a mut Value>{<&str as ValueIndex>::get_mut(*self,v)}fn insert<'a>(self,v:&'a mut Value)->&'a mut Value{<&str as ValueIndex>::insert(*self,v)}}
impl ValueIndex for String{fn get<'a>(self,v:&'a Value)->Option<&'a Value>{v.as_object()?.get(&self)}fn get_mut<'a>(self,v:&'a mut Value)->Option<&'a mut Value>{v.as_object_mut()?.get_mut(&self)}fn insert<'a>(self,v:&'a mut Value)->&'a mut Value{if v.is_null(){*v=Value::Object(Map::new());}v.as_object_mut().expect("JSON field requires object").entry(self).or_default()}}
impl ValueIndex for usize{fn get<'a>(self,v:&'a Value)->Option<&'a Value>{v.as_array()?.get(self)}fn get_mut<'a>(self,v:&'a mut Value)->Option<&'a mut Value>{v.as_array_mut()?.get_mut(self)}fn insert<'a>(self,v:&'a mut Value)->&'a mut Value{&mut v.as_array_mut().expect("JSON offset requires array")[self]}}
impl<I:ValueIndex> Index<I> for Value{type Output=Value;fn index(&self,index:I)->&Self::Output{index.get(self).unwrap_or(&Value::Null)}}
impl<I:ValueIndex> IndexMut<I> for Value{fn index_mut(&mut self,index:I)->&mut Self::Output{index.insert(self)}}
impl From<Map<String,Value>> for Value{fn from(v:Map<String,Value>)->Self{Self::Object(v)}}
impl From<Vec<Value>> for Value{fn from(v:Vec<Value>)->Self{Self::Array(v)}}
impl From<String> for Value{fn from(v:String)->Self{Self::String(v)}}
impl From<&str> for Value{fn from(v:&str)->Self{Self::String(v.into())}}
impl From<bool> for Value{fn from(v:bool)->Self{Self::Bool(v)}}
impl fmt::Display for Value{fn fmt(&self,f:&mut fmt::Formatter<'_>)->fmt::Result{let s=to_string(self).map_err(|_|fmt::Error)?;f.write_str(&s)}}
macro_rules! equality {($t:ty,$method:ident)=>{impl PartialEq<$t> for Value{fn eq(&self,rhs:&$t)->bool{self.$method().is_some_and(|v|v==*rhs)}}impl PartialEq<Value> for $t{fn eq(&self,rhs:&Value)->bool{rhs==self}}};}
impl PartialEq<bool> for &Value{fn eq(&self,rhs:&bool)->bool{self.as_bool()==Some(*rhs)}}
equality!(bool,as_bool);equality!(i64,as_i64);equality!(u64,as_u64);equality!(f64,as_f64);
impl PartialEq<i32> for Value{fn eq(&self,v:&i32)->bool{self.as_i64()==Some(i64::from(*v))}}
impl PartialEq<Value> for i32{fn eq(&self,v:&Value)->bool{v==self}}
impl PartialEq<str> for Value{fn eq(&self,v:&str)->bool{self.as_str()==Some(v)}}
impl PartialEq<&str> for Value{fn eq(&self,v:&&str)->bool{self.as_str()==Some(*v)}}
impl PartialEq<String> for Value{fn eq(&self,v:&String)->bool{self.as_str()==Some(v.as_str())}}
impl PartialEq<Value> for str{fn eq(&self,v:&Value)->bool{v==self}}
impl PartialEq<Value> for &str{fn eq(&self,v:&Value)->bool{v==self}}
impl PartialEq<Value> for String{fn eq(&self,v:&Value)->bool{v==self}}
thread_local!{static MODE:Cell<u8>=const{Cell::new(0)};static CAPTURES:RefCell<Vec<Value>>=const{RefCell::new(Vec::new())};}
struct Scope{mode:u8,length:usize}
impl Scope{fn new(mode:u8)->Self{let previous=MODE.with(|v|v.replace(mode));let length=CAPTURES.with(|v|v.borrow().len());Self{mode:previous,length}}}
impl Drop for Scope{fn drop(&mut self){CAPTURES.with(|v|v.borrow_mut().truncate(self.length));MODE.with(|v|v.set(self.mode));}}
fn capture(v:&Value)->usize{CAPTURES.with(|values|{let mut values=values.borrow_mut();let id=values.len();values.push(v.clone());id})}
fn captured(id:usize)->Result<Value>{CAPTURES.with(|v|v.borrow().get(id).cloned()).ok_or_else(||error("invalid internal value reference"))}
pub(crate) fn error(message:impl fmt::Display)->Error{<Error as serde::ser::Error>::custom(message)}
impl Serialize for Value{
    fn serialize<S:serde::Serializer>(&self,s:S)->std::result::Result<S::Ok,S::Error>{
        if MODE.with(|v|v.get()==1){return s.serialize_newtype_struct(MARKER,&capture(self));}
        match self.kind(){None=>s.serialize_unit(),Some(Kind::Bool(v))=>s.serialize_bool(*v),Some(Kind::Number(v))=>v.serialize(s),
            Some(Kind::String(v))=>s.serialize_str(v),Some(Kind::Array(v))=>v.serialize(s),Some(Kind::Object(v))=>v.serialize(s)}
    }
}
pub fn to_value<T:Serialize>(v:T)->Result<Value>{let _scope=Scope::new(1);v.serialize(ser::Serializer)}
pub fn from_value<T:serde::de::DeserializeOwned>(v:Value)->Result<T>{let _scope=Scope::new(2);T::deserialize(de::Decoder(wire::unpack(v)?))}
pub fn to_vec<T:Serialize+?Sized>(v:&T)->Result<Vec<u8>>{let _scope=Scope::new(0);serde_json::to_vec(v)}
pub fn to_vec_pretty<T:Serialize+?Sized>(v:&T)->Result<Vec<u8>>{let _scope=Scope::new(0);serde_json::to_vec_pretty(v)}
pub fn to_string<T:Serialize+?Sized>(v:&T)->Result<String>{let _scope=Scope::new(0);serde_json::to_string(v)}
pub fn to_string_pretty<T:Serialize+?Sized>(v:&T)->Result<String>{let _scope=Scope::new(0);serde_json::to_string_pretty(v)}
pub fn from_slice<'a,T:Deserialize<'a>>(v:&'a[u8])->Result<T>{let _scope=Scope::new(0);serde_json::from_slice(v)}
pub fn from_str<'a,T:Deserialize<'a>>(v:&'a str)->Result<T>{let _scope=Scope::new(0);serde_json::from_str(v)}
pub fn from_reader<R:std::io::Read,T:serde::de::DeserializeOwned>(v:R)->Result<T>{let _scope=Scope::new(0);serde_json::from_reader(v)}
pub fn to_writer<W:std::io::Write,T:Serialize+?Sized>(w:W,v:&T)->Result<()>{let _scope=Scope::new(0);serde_json::to_writer(w,v)}
pub fn to_writer_pretty<W:std::io::Write,T:Serialize+?Sized>(w:W,v:&T)->Result<()>{let _scope=Scope::new(0);serde_json::to_writer_pretty(w,v)}
