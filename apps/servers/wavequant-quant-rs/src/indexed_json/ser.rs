use super::{Value,Number,Map,Error,Result,error,MARKER,captured};
use serde::{Serialize,ser::{self,SerializeSeq,SerializeMap}};
pub(super) struct Serializer;
impl ser::Serializer for Serializer{
    type Ok=Value;type Error=Error;type SerializeSeq=Sequence;type SerializeTuple=Sequence;type SerializeTupleStruct=Sequence;
    type SerializeTupleVariant=TupleVariant;type SerializeMap=Object;type SerializeStruct=Object;type SerializeStructVariant=ObjectVariant;
    fn serialize_bool(self,v:bool)->Result<Value>{Ok(Value::Bool(v))}
    fn serialize_i8(self,v:i8)->Result<Value>{self.serialize_i64(v.into())}
    fn serialize_i16(self,v:i16)->Result<Value>{self.serialize_i64(v.into())}
    fn serialize_i32(self,v:i32)->Result<Value>{self.serialize_i64(v.into())}
    fn serialize_i64(self,v:i64)->Result<Value>{Ok(Value::Number(v.into()))}
    fn serialize_i128(self,v:i128)->Result<Value>{Number::from_i128(v).map(Value::Number).ok_or_else(||error("integer exceeds JSON range"))}
    fn serialize_u8(self,v:u8)->Result<Value>{self.serialize_u64(v.into())}
    fn serialize_u16(self,v:u16)->Result<Value>{self.serialize_u64(v.into())}
    fn serialize_u32(self,v:u32)->Result<Value>{self.serialize_u64(v.into())}
    fn serialize_u64(self,v:u64)->Result<Value>{Ok(Value::Number(v.into()))}
    fn serialize_u128(self,v:u128)->Result<Value>{Number::from_u128(v).map(Value::Number).ok_or_else(||error("integer exceeds JSON range"))}
    fn serialize_f32(self,v:f32)->Result<Value>{self.serialize_f64(v.into())}
    fn serialize_f64(self,v:f64)->Result<Value>{Ok(Number::from_f64(v).map_or(Value::Null,Value::Number))}
    fn serialize_char(self,v:char)->Result<Value>{self.serialize_str(&v.to_string())}
    fn serialize_str(self,v:&str)->Result<Value>{Ok(Value::String(v.into()))}
    fn serialize_bytes(self,v:&[u8])->Result<Value>{v.serialize(self)}
    fn serialize_none(self)->Result<Value>{Ok(Value::Null)}
    fn serialize_some<T:Serialize+?Sized>(self,v:&T)->Result<Value>{v.serialize(self)}
    fn serialize_unit(self)->Result<Value>{Ok(Value::Null)}
    fn serialize_unit_struct(self,_:&'static str)->Result<Value>{Ok(Value::Null)}
    fn serialize_unit_variant(self,_:&'static str,_:u32,v:&'static str)->Result<Value>{Ok(Value::String(v.into()))}
    fn serialize_newtype_struct<T:Serialize+?Sized>(self,name:&'static str,v:&T)->Result<Value>{
        let value=v.serialize(self)?;if name==MARKER{captured(value.as_u64().and_then(|v|usize::try_from(v).ok()).ok_or_else(||error("invalid value handle"))?)}else{Ok(value)}
    }
    fn serialize_newtype_variant<T:Serialize+?Sized>(self,_:&'static str,_:u32,name:&'static str,v:&T)->Result<Value>{Ok(Value::Object(Map::from([(name.into(),v.serialize(self)?)])))}
    fn serialize_seq(self,len:Option<usize>)->Result<Sequence>{Ok(Sequence(Vec::with_capacity(len.unwrap_or(0))))}
    fn serialize_tuple(self,len:usize)->Result<Sequence>{self.serialize_seq(Some(len))}
    fn serialize_tuple_struct(self,_:&'static str,len:usize)->Result<Sequence>{self.serialize_seq(Some(len))}
    fn serialize_tuple_variant(self,_:&'static str,_:u32,name:&'static str,len:usize)->Result<TupleVariant>{Ok(TupleVariant{name,sequence:Sequence(Vec::with_capacity(len))})}
    fn serialize_map(self,_:Option<usize>)->Result<Object>{Ok(Object{fields:Map::new(),key:None})}
    fn serialize_struct(self,_:&'static str,len:usize)->Result<Object>{self.serialize_map(Some(len))}
    fn serialize_struct_variant(self,_:&'static str,_:u32,name:&'static str,len:usize)->Result<ObjectVariant>{Ok(ObjectVariant{name,object:self.serialize_struct(name,len)?})}
    fn collect_str<T:std::fmt::Display+?Sized>(self,v:&T)->Result<Value>{self.serialize_str(&v.to_string())}
}
pub(super) struct Sequence(Vec<Value>);
impl SerializeSeq for Sequence{type Ok=Value;type Error=Error;
    fn serialize_element<T:Serialize+?Sized>(&mut self,v:&T)->Result<()>{self.0.push(v.serialize(Serializer)?);Ok(())}
    fn end(self)->Result<Value>{Ok(Value::Array(self.0))}
}
impl ser::SerializeTuple for Sequence{type Ok=Value;type Error=Error;fn serialize_element<T:Serialize+?Sized>(&mut self,v:&T)->Result<()>{SerializeSeq::serialize_element(self,v)}fn end(self)->Result<Value>{SerializeSeq::end(self)}}
impl ser::SerializeTupleStruct for Sequence{type Ok=Value;type Error=Error;fn serialize_field<T:Serialize+?Sized>(&mut self,v:&T)->Result<()>{SerializeSeq::serialize_element(self,v)}fn end(self)->Result<Value>{SerializeSeq::end(self)}}
pub(super) struct TupleVariant{name:&'static str,sequence:Sequence}
impl ser::SerializeTupleVariant for TupleVariant{type Ok=Value;type Error=Error;fn serialize_field<T:Serialize+?Sized>(&mut self,v:&T)->Result<()>{self.sequence.serialize_element(v)}fn end(self)->Result<Value>{Ok(Value::Object(Map::from([(self.name.into(),SerializeSeq::end(self.sequence)?)])))}}
pub(super) struct Object{fields:Map<String,Value>,key:Option<String>}
impl SerializeMap for Object{type Ok=Value;type Error=Error;
    fn serialize_key<T:Serialize+?Sized>(&mut self,v:&T)->Result<()>{
        let value=v.serialize(Serializer)?;self.key=Some(if let Some(s)=value.as_str(){s.into()}else if let Some(n)=value.as_number(){n.to_string()}else if let Some(b)=value.as_bool(){b.to_string()}else{return Err(error("JSON map key must be scalar"));});Ok(())
    }
    fn serialize_value<T:Serialize+?Sized>(&mut self,v:&T)->Result<()>{let key=self.key.take().ok_or_else(||error("JSON value without key"))?;self.fields.insert(key,v.serialize(Serializer)?);Ok(())}
    fn end(self)->Result<Value>{Ok(Value::Object(self.fields))}
}
impl ser::SerializeStruct for Object{type Ok=Value;type Error=Error;fn serialize_field<T:Serialize+?Sized>(&mut self,k:&'static str,v:&T)->Result<()>{self.fields.insert(k.into(),v.serialize(Serializer)?);Ok(())}fn end(self)->Result<Value>{SerializeMap::end(self)}}
pub(super) struct ObjectVariant{name:&'static str,object:Object}
impl ser::SerializeStructVariant for ObjectVariant{type Ok=Value;type Error=Error;fn serialize_field<T:Serialize+?Sized>(&mut self,k:&'static str,v:&T)->Result<()>{ser::SerializeStruct::serialize_field(&mut self.object,k,v)}fn end(self)->Result<Value>{Ok(Value::Object(Map::from([(self.name.into(),SerializeMap::end(self.object)?)])))}}
