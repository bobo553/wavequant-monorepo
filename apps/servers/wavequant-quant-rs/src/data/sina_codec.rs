//! 新浪 KLC2 日线位流解码，按 AKShare 安装包中的 hk_js_decode 协议重写。
use chrono::{Duration,NaiveDate};
use super::tdx::{DailyBar,DataError};
fn invalid(s: &str) -> DataError { DataError::Invalid(s.into()) }
struct Bits { data: Vec<u8>, offset: usize }
impl Bits {
    fn new(text: &str) -> Result<Self,DataError> {
        let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let data = text.bytes().map(|b|alphabet.iter().position(|c|*c == b).map(|n|n as u8).ok_or_else(||invalid("invalid KLC alphabet"))).collect::<Result<Vec<_>,_>>()?;
        if data.len() > 16*1024*1024 { return Err(invalid("KLC input too large")); }
        Ok(Self{data,offset:0})
    }
    fn read(&mut self,width: i32,signed: bool) -> Result<f64,DataError> {
        if !(0..=53).contains(&width) { return Err(invalid("KLC bit width exceeds exact numeric range")); }
        let width = width as usize;
        if self.offset+width > self.data.len()*6 { return Err(invalid("truncated KLC data")); }
        let mut value = 0_u64;
        for bit in 0..width { value |= u64::from((self.data[self.offset/6] >> (self.offset%6)) & 1) << bit; self.offset += 1; }
        if signed && width > 0 && value & (1_u64 << (width-1)) != 0 { Ok(value as f64 - (1_u64 << width) as f64) } else { Ok(value as f64) }
    }
    fn bit(&mut self) -> Result<bool,DataError> { Ok(self.read(1,false)? != 0.0) }
    fn delta(&mut self) -> Result<i32,DataError> {
        let sign = if self.bit()? {1} else {-1};
        let mut count = 1;
        while self.bit()? { count += 1; if count > 64 { return Err(invalid("KLC delta too large")); } }
        Ok(sign*count)
    }
}
fn parts(n: i32) -> (i32,i32) {
    if n < 0 { let (a,b)=parts(-n); return (-a,-b); }
    let q = n/3;
    (q+i32::from(n%3 == 1),q+i32::from(n%3 == 2))
}
fn scale(n: i32) -> Result<f64,DataError> {
    if !(-60..=60).contains(&n) { return Err(invalid("KLC precision out of range")); }
    let (a,b) = parts(n);
    Ok(2_f64.powi(a)*5_f64.powi(b))
}
fn rescale(value: f64,old: i32,new: i32) -> Result<f64,DataError> { Ok((value*scale(new)?/scale(old)?).round_ties_even()) }
fn advance(day: &mut i32,count: i32,weekdays: u32) -> Result<String,DataError> {
    if !(0..=100000).contains(&count) || weekdays & 127 == 0 { return Err(invalid("KLC date step invalid")); }
    for _ in 0..count {
        loop { *day = day.checked_add(1).ok_or_else(||invalid("KLC date overflow"))?; if weekdays & (1 << (*day+10).rem_euclid(7)) != 0 { break; } }
    }
    let origin = NaiveDate::from_ymd_opt(1970,1,1).ok_or_else(||invalid("invalid epoch"))?;
    origin.checked_add_signed(Duration::days(i64::from(*day)+7657)).map(|d|d.to_string()).ok_or_else(||invalid("KLC date overflow"))
}
fn date_step(bits: &mut Bits,day: &mut i32) -> Result<i32,DataError> {
    let value = bits.read(3,false)? as i32;
    match value { 0 => Ok(bits.read(6,false)? as i32), 1 => { *day=bits.read(18,true)? as i32; Ok(0) }, _ => Ok(value) }
}
/// 完整日线协议 3466。其他编码类型明确拒绝，不把分钟/收盘序列伪装成 OHLC。
pub fn decode_daily(text: &str) -> Result<Vec<DailyBar>,DataError> {
    let mut bits = Bits::new(text)?;
    let kind = bits.read(12,false)? as u32;
    let version = 63 ^ bits.read(6,false)? as u32;
    if kind != 3466 || version != 0 { return Err(invalid("unsupported Sina KLC daily format/version")); }
    let mut average_amount = true;
    let mut post = false;
    let mut post_extra = false;
    let mut separate = false;
    let mut precision = [6,0,0,0,0];
    let mut widths = [3,3,3,3,5,5,3,0];
    let mut units = [0.0;5];
    let mut prices = [0.0;4];
    let mut weekdays = 62;
    let mut day = 0;
    let mut result: Vec<DailyBar> = Vec::new();
    loop {
        let mut step = 1;
        if bits.bit()? {
            if bits.bit()? {
                let mut control_count = 0;
                if bits.bit()? {
                    control_count += 1;
                    let previous_average = average_amount;
                    if bits.bit()? {
                        average_amount ^= bits.bit()?;
                        post ^= bits.bit()?;
                        post_extra ^= bits.bit()?;
                        let old_separate = separate;
                        separate ^= bits.bit()?;
                        if bits.bit()? { weekdays = bits.read(7,false)? as u32; }
                        if old_separate != separate { if old_separate { units[0]=prices[3]; } else { prices.fill(units[0]); } }
                    }
                    for index in 0..(3+2*usize::from(post)) {
                        if bits.bit()? {
                            let old = precision[index];
                            precision[index] += bits.delta()?;
                            units[index] = rescale(units[index],old,precision[index])?;
                            if separate && index == 0 { for price in &mut prices { *price=rescale(*price,old,precision[0])?; } }
                        }
                    }
                    if !average_amount && previous_average { units[2] = rescale(result.last().map_or(0.0,|bar|bar.amount),0,precision[2])?; }
                }
                if bits.bit()? {
                    control_count += 1;
                    for index in 0..(7+usize::from(post)+usize::from(post_extra)) {
                        if bits.bit()? {
                            if index == 6 { step = date_step(&mut bits,&mut day)?; }
                            else { let slot=if index > 6 {index-1} else {index}; widths[slot] += bits.delta()?; }
                        }
                    }
                }
                if bits.bit()? {
                    control_count += 1;
                    let width = widths[0] + if bits.bit()? {bits.delta()?} else {0};
                    let change = bits.read(3*width,true)?;
                    if !separate { units[0] += change; }
                }
                if control_count == 0 { break; }
            } else if bits.bit()? {
                if bits.bit()? { if bits.bit()? { step=date_step(&mut bits,&mut day)?; } else { widths[4]+=bits.delta()?; } }
                else if post && bits.bit()? { let slot=if post_extra && bits.bit()? {7} else {6}; widths[slot]+=bits.delta()?; }
                else { widths[5]+=bits.delta()?; }
            } else { let slot=bits.read(2,false)? as usize; widths[slot]+=bits.delta()?; }
        }
        let mut values = [0.0;8];
        let mask = if separate {191} else {185};
        for index in 0..(6+usize::from(post)+usize::from(post_extra)) { values[index]=bits.read(3*widths[index],mask & (1<<index) != 0)?; }
        let date = advance(&mut day,step,weekdays)?;
        let opening;
        if separate { for index in 0..4 { prices[index]+=values[index]; } opening=prices[0]; }
        else { opening=units[0]+values[0]; prices=[opening,opening+values[1],opening-values[2],opening+values[3]]; units[0]=prices[3]; }
        units[1]+=values[4];
        let amount = if average_amount {
            let average = if separate {prices.iter().sum::<f64>()/4.0} else {opening+(values[1]-values[2]+values[3])/4.0};
            let product = (average*units[1]+0.5).floor();
            let rounded = (product*scale(precision[2])?/(scale(precision[0])?*scale(precision[1])?)).round_ties_even();
            (rounded+values[5])/scale(precision[2])?
        } else { units[2]+=values[5]; units[2]/scale(precision[2])? };
        let price_scale=scale(precision[0])?;
        let volume=units[1]/scale(precision[1])?;
        if volume < 0.0 || !volume.is_finite() || volume.fract() != 0.0 || volume > u64::MAX as f64 { return Err(invalid("invalid KLC volume")); }
        let bar=DailyBar{date,open:prices[0]/price_scale,high:prices[1]/price_scale,low:prices[2]/price_scale,close:prices[3]/price_scale,volume:volume as u64,amount};
        if [bar.open,bar.high,bar.low,bar.close,bar.amount].iter().any(|v|!v.is_finite()) || bar.low <= 0.0 || bar.low > bar.open.min(bar.close) || bar.high < bar.open.max(bar.close) || result.last().is_some_and(|b|b.date >= bar.date) { return Err(invalid("invalid KLC OHLC/date")); }
        result.push(bar);
        if result.len() > 100000 { return Err(invalid("KLC daily record limit exceeded")); }
    }
    if result.is_empty() { return Err(invalid("empty KLC daily data")); }
    Ok(result)
}

/// 新浪交易日历类型 139，包含上游已记录的 1992-05-04 补项。
pub fn decode_calendar(text: &str) -> Result<Vec<String>,DataError> {
    let mut bits=Bits::new(text)?;
    if bits.read(12,false)? as u32 != 139 || (63 ^ bits.read(6,false)? as u32)>1 { return Err(invalid("unsupported Sina calendar format/version")); }
    let mut width=0;
    let mut remaining=-1_i64;
    let mut day=bits.read(18,false)? as i32-1;
    let last=bits.read(18,false)? as i32;
    let mut result=Vec::new();
    let mut initialized=false;
    while day<last {
        let date=advance(&mut day,1,62)?;
        if remaining<=0 {
            if bits.bit()? { width+=bits.delta()?; }
            remaining=bits.read(3*width,false)? as i64+1;
            if !initialized { result.push(date); initialized=true; remaining-=1; }
        } else { result.push(date); }
        remaining-=1;
        if result.len()>20000 { return Err(invalid("Sina calendar exceeds record limit")); }
    }
    if !result.iter().any(|v|v=="1992-05-04") { result.push("1992-05-04".into()); }
    result.sort();
    result.dedup();
    Ok(result)
}

#[derive(Clone,Debug,serde::Serialize)]
pub struct LegacyDailyBar {
    pub date:String,pub open:f64,pub high:f64,pub low:f64,pub close:f64,pub volume:u64,
}
/// 老日线 1479 本身不编码成交额；单独返回，不能用估算成交额冒充现代响应。
pub fn decode_legacy_daily(text:&str)->Result<Vec<LegacyDailyBar>,DataError> {
    let mut bits=Bits::new(text)?;
    if bits.read(12,false)? as u32!=1479 || (63 ^ bits.read(6,false)? as u32)!=0 {return Err(invalid("unsupported legacy Sina daily format/version"));}
    let precision=bits.read(6,false)? as i32;
    if precision>12 {return Err(invalid("legacy Sina precision exceeds range"));}
    let mut day=bits.read(18,true)? as i32-1;
    let divisor=10_f64.powi(precision);
    let md=bits.read(3,false)? as i32;
    let mv=bits.read(3,false)? as i32;
    let mut length_d=0;
    let mut length_v=0;
    let mut previous_close=0.0;
    let mut volume=0.0;
    let masks=[0,3,5,6,9,10,12,15,17,18,20,23,24,27,29,30];
    let mut result=Vec::new();
    while bits.offset+6<=bits.data.len()*6 {
        let control=bits.read(6,false)? as u32;
        let mut step=1;
        let (mut use_d,mut use_v)=(None,None);
        if control & 32!=0 {
            loop {
                let value=bits.read(6,false)? as u32;
                if value|16==63 {
                    let d=bits.read(3,false)? as i32+md;
                    let v=bits.read(3,false)? as i32+mv;
                    if value & 16!=0 {length_d=d;length_v=v;} else {use_d=Some(d);use_v=Some(v);}
                    break;
                }
                if value & 32!=0 {
                    let is_price=value & 8!=0;
                    let width=(value & 7) as i32+if is_price {md} else {mv};
                    match (is_price,value & 16!=0) {(true,true)=>length_d=width,(false,true)=>length_v=width,(true,false)=>use_d=Some(width),(false,false)=>use_v=Some(width)}
                    break;
                }
                step=(value & 15) as i32;
                if step==0 {step=bits.read(6,false)? as i32;}
                else if step==1 {day=bits.read(18,false)? as i32;step=0;}
                if value & 16==0 {break;}
            }
        }
        let date=advance(&mut day,step,62)?;
        let price_width=use_d.unwrap_or(length_d);
        let volume_width=use_v.unwrap_or(length_v);
        let mut widths=[price_width,price_width,price_width,price_width,volume_width];
        let mut mask=masks[(control & 15) as usize];
        if volume_width & 1!=0 {mask=31-mask;}
        if control & 16!=0 {widths[4]+=2;}
        let mut values=[0.0;5];
        for index in 0..5 {
            if mask & (1<<(4-index))!=0 {widths[index]+=1;}
            values[index]=bits.read(widths[index]*3,matches!(index,0 | 3 | 4))?;
        }
        let open=previous_close+values[0];
        previous_close=open+values[3];
        volume+=values[4];
        if !volume.is_finite() || volume<0.0 || volume.fract()!=0.0 || volume>u64::MAX as f64 {return Err(invalid("legacy Sina volume invalid"));}
        let bar=LegacyDailyBar{date,open:open/divisor,high:(open+values[1])/divisor,low:(open-values[2])/divisor,close:previous_close/divisor,volume:volume as u64};
        if bar.low<=0.0 || bar.low>bar.open.min(bar.close) || bar.high<bar.open.max(bar.close) {return Err(invalid("legacy Sina OHLC invalid"));}
        result.push(bar);
        if result.len()>100000 {return Err(invalid("legacy Sina records exceed limit"));}
    }
    if result.is_empty() {return Err(invalid("legacy Sina daily payload empty"));}
    Ok(result)
}
