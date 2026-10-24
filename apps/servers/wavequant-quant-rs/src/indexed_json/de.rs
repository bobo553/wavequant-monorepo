use super::{Value,Kind,Map,Error,Result,MARKER,MODE,capture,captured,error};
use serde::{Deserialize,Deserializer,de::{self,Visitor,DeserializeSeed}};
use std::fmt;
struct ValueVisitor;
impl<'de> Visitor<'de> for ValueVisitor{
    type Value=Value;fn expecting(&self,f:&mut fmt::Formatter)->fmt::Result{f.write_str("JSON value or internal node reference")}
    fn visit_unit<E:de::Error>(self)->std::result::Result<Value,E>{Ok(Value::Null)}
    fn visit_none<E:de::Error>(self)->std::result::Result<Value,E>{Ok(Value::Null)}
    fn visit_bool<E:de::Error>(self,v:bool)->std::result::Result<Value,E>{Ok(Value::Bool(v))}
    fn visit_i64<E:de::Error>(self,v:i64)->std::result::Result<Value,E>{Ok(Value::Number(v.into()))}
    fn visit_u64<E:de::Error>(self,v:u64)->std::result::Result<Value,E>{Ok(Value::Number(v.into()))}
    fn visit_f64<E:de::Error>(self,v:f64)->std::result::Result<Value,E>{Ok(super::Number::from_f64(v).map_or(Value::Null,Value::Number))}
    fn visit_str<E:de::Error>(self,v:&str)->std::result::Result<Value,E>{Ok(Value::String(v.into()))}
    fn visit_string<E:de::Error>(self,v:String)->std::result::Result<Value,E>{Ok(Value::String(v))}
    fn visit_seq<A:de::SeqAccess<'de>>(self,mut a:A)->std::result::Result<Value,A::Error>{let mut values=Vec::new();while let Some(v)=a.next_element()?{values.push(v);}Ok(Value::Array(values))}
    fn visit_map<A:de::MapAccess<'de>>(self,mut a:A)->std::result::Result<Value,A::Error>{let mut values=Map::new();while let Some((k,v))=a.next_entry()?{values.insert(k,v);}super::wire::unpack(Value::Object(values)).map_err(de::Error::custom)}
}
struct HandleVisitor;
impl<'de> Visitor<'de> for HandleVisitor{
    type Value=Value;fn expecting(&self,f:&mut fmt::Formatter)->fmt::Result{f.write_str("internal value index")}
    fn visit_u64<E:de::Error>(self,v:u64)->std::result::Result<Value,E>{captured(usize::try_from(v).map_err(E::custom)?).map_err(E::custom)}
    fn visit_newtype_struct<D:Deserializer<'de>>(self,d:D)->std::result::Result<Value,D::Error>{
        let _scope=super::Scope::new(0);d.deserialize_any(ValueVisitor)
    }
}
impl<'de> Deserialize<'de> for Value{fn deserialize<D:Deserializer<'de>>(d:D)->std::result::Result<Self,D::Error>{
    if MODE.with(|v|v.get()==2){d.deserialize_newtype_struct(MARKER,HandleVisitor)}else{d.deserialize_any(ValueVisitor)}
}}
pub(super) struct Decoder(pub Value);
impl<'de> Deserializer<'de> for Decoder{
    type Error=Error;
    fn deserialize_any<V:Visitor<'de>>(self,v:V)->Result<V::Value>{match self.0.kind(){
        None=>v.visit_unit(),Some(Kind::Bool(b))=>v.visit_bool(*b),Some(Kind::String(s))=>v.visit_string(s.clone()),
        Some(Kind::Number(n))=>if let Some(i)=n.as_u64(){v.visit_u64(i)}else if let Some(i)=n.as_i64(){v.visit_i64(i)}else{v.visit_f64(n.as_f64().ok_or_else(||error("invalid number"))?)},
        Some(Kind::Array(items))=>v.visit_seq(Sequence(items.clone().into_iter())),
        Some(Kind::Object(fields))=>v.visit_map(Object{fields:fields.clone().into_iter(),next:None}),
    }}
    fn deserialize_option<V:Visitor<'de>>(self,v:V)->Result<V::Value>{if self.0.is_null(){v.visit_none()}else{v.visit_some(self)}}
    fn deserialize_newtype_struct<V:Visitor<'de>>(self,name:&'static str,v:V)->Result<V::Value>{if name==MARKER{v.visit_u64(capture(&self.0) as u64)}else{v.visit_newtype_struct(self)}}
    fn deserialize_enum<V:Visitor<'de>>(self,_:&'static str,_:&'static[&'static str],v:V)->Result<V::Value>{
        if let Some(name)=self.0.as_str(){return v.visit_enum(de::value::StringDeserializer::<Error>::new(name.into()));}
        let fields=self.0.as_object().filter(|v|v.len()==1).ok_or_else(||error("enum requires one variant"))?;
        let(name,value)=fields.iter().next().ok_or_else(||error("missing enum variant"))?;v.visit_enum(EnumNode{name:name.clone(),value:value.clone()})
    }
    serde::forward_to_deserialize_any!{bool i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 f32 f64 char str string bytes byte_buf unit unit_struct seq tuple tuple_struct map struct identifier ignored_any}
}
struct Sequence(std::vec::IntoIter<Value>);
impl<'de> de::SeqAccess<'de> for Sequence{type Error=Error;fn next_element_seed<T:DeserializeSeed<'de>>(&mut self,seed:T)->Result<Option<T::Value>>{self.0.next().map(|v|seed.deserialize(Decoder(v))).transpose()}fn size_hint(&self)->Option<usize>{Some(self.0.len())}}
struct Object{fields:std::collections::btree_map::IntoIter<String,Value>,next:Option<Value>}
impl<'de> de::MapAccess<'de> for Object{type Error=Error;
    fn next_key_seed<T:DeserializeSeed<'de>>(&mut self,seed:T)->Result<Option<T::Value>>{match self.fields.next(){Some((key,value))=>{self.next=Some(value);seed.deserialize(KeyDecoder(key)).map(Some)},None=>Ok(None)}}
    fn next_value_seed<T:DeserializeSeed<'de>>(&mut self,seed:T)->Result<T::Value>{seed.deserialize(Decoder(self.next.take().ok_or_else(||error("missing map value"))?))}
    fn size_hint(&self)->Option<usize>{Some(self.fields.len())}
}
struct KeyDecoder(String);
macro_rules! numeric_key {($name:ident,$visit:ident,$t:ty)=>{fn $name<V:Visitor<'de>>(self,v:V)->Result<V::Value>{let n=self.0.parse::<$t>().map_err(error)?;v.$visit(n)}};}
impl<'de> Deserializer<'de> for KeyDecoder{
    type Error=Error;
    fn deserialize_any<V:Visitor<'de>>(self,v:V)->Result<V::Value>{v.visit_string(self.0)}
    numeric_key!(deserialize_bool,visit_bool,bool);
    numeric_key!(deserialize_i8,visit_i8,i8);numeric_key!(deserialize_i16,visit_i16,i16);numeric_key!(deserialize_i32,visit_i32,i32);
    numeric_key!(deserialize_i64,visit_i64,i64);numeric_key!(deserialize_i128,visit_i128,i128);
    numeric_key!(deserialize_u8,visit_u8,u8);numeric_key!(deserialize_u16,visit_u16,u16);numeric_key!(deserialize_u32,visit_u32,u32);
    numeric_key!(deserialize_u64,visit_u64,u64);numeric_key!(deserialize_u128,visit_u128,u128);
    numeric_key!(deserialize_f32,visit_f32,f32);numeric_key!(deserialize_f64,visit_f64,f64);
    fn deserialize_newtype_struct<V:Visitor<'de>>(self,_:&'static str,v:V)->Result<V::Value>{v.visit_newtype_struct(self)}
    fn deserialize_enum<V:Visitor<'de>>(self,name:&'static str,variants:&'static[&'static str],v:V)->Result<V::Value>{de::value::StringDeserializer::<Error>::new(self.0).deserialize_enum(name,variants,v)}
    serde::forward_to_deserialize_any!{char str string bytes byte_buf option unit unit_struct seq tuple tuple_struct map struct identifier ignored_any}
}
struct EnumNode{name:String,value:Value}
impl<'de> de::EnumAccess<'de> for EnumNode{type Error=Error;type Variant=Decoder;fn variant_seed<V:DeserializeSeed<'de>>(self,seed:V)->Result<(V::Value,Decoder)>{Ok((seed.deserialize(de::value::StringDeserializer::<Error>::new(self.name))?,Decoder(self.value)))}}
impl<'de> de::VariantAccess<'de> for Decoder{type Error=Error;fn unit_variant(self)->Result<()>{Deserialize::deserialize(self)}fn newtype_variant_seed<T:DeserializeSeed<'de>>(self,seed:T)->Result<T::Value>{seed.deserialize(self)}fn tuple_variant<V:Visitor<'de>>(self,_:usize,v:V)->Result<V::Value>{self.deserialize_any(v)}fn struct_variant<V:Visitor<'de>>(self,_:&'static[&'static str],v:V)->Result<V::Value>{self.deserialize_any(v)}}
