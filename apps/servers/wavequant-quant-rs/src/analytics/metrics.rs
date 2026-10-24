use super::{AnalyticsResult,float,number,date};
use crate::execution::{StrategyBacktest,BacktestTrade};
use rust_decimal::Decimal;
use crate::indexed_json::{Value,json};
use std::collections::BTreeMap;

pub fn equity_metrics(curve:&[Value],capital:Decimal)->AnalyticsResult<Value>{
    let capital=float(capital)?;if capital<=0.0{return Err("positive capital required".into());}
    let mut daily=BTreeMap::new();
    for row in curve{let stamp=row.get("timestamp").and_then(Value::as_str).ok_or("equity timestamp missing")?;
        let equity=row.get("equity").and_then(number).ok_or("finite equity required")?;
        if equity<=0.0{return Err("positive daily equity required".into());}daily.insert(date(stamp).to_string(),row);}
    let(mut previous,mut peak,mut drawdown)=(capital,capital,0.0_f64);let mut returns=vec![];let mut exposure=vec![];
    for row in daily.values(){let value=number(&row["equity"]).ok_or("equity missing")?;returns.push(value/previous-1.0);previous=value;peak=peak.max(value);drawdown=drawdown.min(value/peak-1.0);
        if let Some(value)=row.get("exposure").and_then(number){exposure.push(value);}}
    let n=returns.len();let mean=if n>0{returns.iter().sum::<f64>()/n as f64}else{0.0};
    let vol=if n>1{(returns.iter().map(|v|(v-mean).powi(2)).sum::<f64>()/(n-1)as f64).sqrt()}else{0.0};
    let annual=if n>0{(previous/capital).powf(252.0/n as f64)-1.0}else{0.0};
    Ok(json!({"total_return":previous/capital-1.0,"annualized_return":if annual.is_finite(){Some(annual)}else{None},
        "max_drawdown":drawdown,"sharpe":if vol>0.0{Some(mean/vol*252.0_f64.sqrt())}else{None},
        "annualized_volatility":vol*252.0_f64.sqrt(),"average_exposure":if exposure.len()==n{Some(if n>0{exposure.iter().sum::<f64>()/n as f64}else{0.0})}else{None},
        "final_equity":previous,"sessions":n}))
}
pub fn summarize_trades(trades:&[BacktestTrade])->AnalyticsResult<Value>{
    let gains=trades.iter().map(|t|t.pnl.max(Decimal::ZERO)).sum::<Decimal>();
    let losses=-trades.iter().map(|t|t.pnl.min(Decimal::ZERO)).sum::<Decimal>();
    let count=trades.len();
    Ok(json!({"trades":count,"win_rate":if count>0{trades.iter().filter(|t|t.pnl>Decimal::ZERO).count()as f64/count as f64}else{0.0},
        "closed_pnl":float(trades.iter().map(|t|t.pnl).sum())?,"average_net_return":if count>0{float(trades.iter().map(|t|t.net_return).sum::<Decimal>()/Decimal::from(count as u64))?}else{0.0},
        "profit_factor":if losses>Decimal::ZERO{Some(float(gains/losses)?)}else{None}}))
}
pub fn account_curve(result:&StrategyBacktest)->AnalyticsResult<Vec<Value>>{
    result.portfolio_days.iter().map(|row|Ok(json!({"timestamp":row.at.to_rfc3339(),"cash":float(row.cash)?,
        "market_value":float(row.equity-row.cash)?,"equity":float(row.equity)?,"exposure":float(row.exposure)?,"positions":row.positions}))).collect()
}
pub fn result_metrics(result:&StrategyBacktest)->AnalyticsResult<Value>{
    let curve=account_curve(result)?;let mut metrics=equity_metrics(&curve,result.config.initial_capital)?;
    super::merge(&mut metrics,&summarize_trades(&result.trades)?);
    metrics["total_fees"]=json!(float(result.total_fees)?);metrics["turnover"]=json!(float(result.turnover)?);Ok(metrics)
}
/// 图表只改变权益结果展示，不再次执行策略。
pub fn chart_curve(curve:&[Value],capital:Decimal)->AnalyticsResult<Vec<Value>>{
    let capital=float(capital)?;if capital<=0.0{return Err("positive capital required".into());}
    let mut peak=capital;curve.iter().map(|row|{let equity=number(&row["equity"]).ok_or("curve equity missing")?;peak=peak.max(equity);
        let mut point=row.clone();point["time"]=json!(date(row["timestamp"].as_str().ok_or("curve timestamp missing")?));
        point["return"]=json!(equity/capital-1.0);point["drawdown"]=json!(equity/peak-1.0);Ok(point)}).collect()
}
