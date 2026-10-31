//! 行情校验与不可变借用绑定，候选复用同一输入的校验结果，不保存全局跳过开关。
use std::ops::Deref;
use super::{RuleCandle,validate_bars};

pub(crate) struct ValidatedCandles {
    bars:Vec<RuleCandle>,
}

#[derive(Clone,Copy)]
pub(crate) struct ValidatedBarSlice<'a> {
    bars:&'a [RuleCandle],
}

impl ValidatedCandles {
    pub(crate) fn new(bars:Vec<RuleCandle>)->Result<Self,String>{
        validate_bars(&bars)?;
        Ok(Self{bars})
    }

    pub(crate) fn prefix(&self,len:usize)->Result<ValidatedBarSlice<'_>,String>{
        self.bars.get(..len).map(|bars|ValidatedBarSlice{bars}).ok_or_else(||"validated prefix outside market array".into())
    }

    /// 原前缀不可修改；追加只验证新增行情及与旧尾部的顺序，失败不改变已验证数据。
    pub(crate) fn append(&mut self,additional:Vec<RuleCandle>)->Result<(),String>{
        validate_bars(&additional)?;
        if let(Some(previous),Some(first))=(self.bars.last(),additional.first()){
            if previous.symbol!=first.symbol||previous.timestamp>=first.timestamp{
                return Err("rules require ordered single-symbol bars".into());
            }
        }
        self.bars.extend(additional);
        Ok(())
    }
}

impl Deref for ValidatedCandles {
    type Target=[RuleCandle];
    fn deref(&self)->&Self::Target{&self.bars}
}

impl<'a> ValidatedBarSlice<'a> {
    pub(crate) fn new(bars:&'a [RuleCandle])->Result<Self,String>{
        validate_bars(bars)?;
        Ok(Self{bars})
    }
    pub(crate) fn as_slice(self)->&'a [RuleCandle]{self.bars}
    pub(crate) fn prefix(self,len:usize)->Result<Self,String>{
        self.bars.get(..len).map(|bars|Self{bars}).ok_or_else(||"validated prefix outside market array".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal::Decimal;
    fn bar(day:usize)->RuleCandle{
        RuleCandle{symbol:"TEST".into(),timestamp:format!("2020-01-{day:02}"),open:Decimal::from(10),
            high:Decimal::from(12),low:Decimal::from(9),close:Decimal::from(11),volume:Decimal::from(100),
            adjustment_factor:Decimal::ONE,raw_trading_active:None,raw_is_st:None}
    }
    #[test]
    fn borrowed_prefix_keeps_original_indices_without_copying(){
        let raw=vec![bar(1),bar(2)];let address=raw.as_ptr();
        let input=ValidatedCandles::new(raw).unwrap();let prefix=input.prefix(1).unwrap();
        assert_eq!(prefix.as_slice().as_ptr(),address);assert_eq!(prefix.as_slice().len(),1);
        assert!(prefix.prefix(2).is_err());
    }
    #[test]
    fn rejected_append_keeps_the_old_prefix_and_checks_the_boundary(){
        let mut input=ValidatedCandles::new(vec![bar(1)]).unwrap();
        for additional in [vec![bar(1)],vec![bar(3),bar(2)]]{
            assert!(input.append(additional).is_err());assert_eq!(input.len(),1);
        }
        let mut foreign=bar(2);foreign.symbol="OTHER".into();
        assert!(input.append(vec![foreign]).is_err());assert_eq!(input.len(),1);
        input.append(vec![bar(2)]).unwrap();assert_eq!(input.len(),2);
    }
    #[test]
    fn validation_cannot_be_reused_for_another_or_mutated_input(){
        let good=vec![bar(1)];let _proof=ValidatedBarSlice::new(&good).unwrap();
        let mut invalid=bar(2);invalid.low=Decimal::from(20);
        assert!(ValidatedCandles::new(vec![invalid.clone()]).is_err());
        assert!(ValidatedBarSlice::new(&[invalid]).is_err());
    }
}
