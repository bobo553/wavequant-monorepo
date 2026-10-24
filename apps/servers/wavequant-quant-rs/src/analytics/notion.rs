//! 本地笔记清单和已完成实验的固定假设结果归并；不访问外链或运行策略。
use super::{AnalyticsResult,number,research};
use crate::indexed_json::{Value,json};
use sha2::{Sha256,Digest};
use std::{collections::{BTreeMap,BTreeSet},path::{Path,PathBuf},fs};
fn files(root:&Path,out:&mut Vec<PathBuf>)->AnalyticsResult<()>{
    for entry in fs::read_dir(root).map_err(|e|e.to_string())?{let entry=entry.map_err(|e|e.to_string())?;let kind=entry.file_type().map_err(|e|e.to_string())?;
        if kind.is_symlink(){continue;}if kind.is_dir(){files(&entry.path(),out)?;}else if kind.is_file()&&entry.path().extension().and_then(|s|s.to_str())==Some("md"){out.push(entry.path());}}
    Ok(())
}
fn unquote(value:&str)->String{let mut bytes=vec![];let input=value.as_bytes();let mut i=0;while i<input.len(){
    if input[i]==b'%'&&i+2<input.len(){if let Some(v)=std::str::from_utf8(&input[i+1..i+3]).ok().and_then(|v|u8::from_str_radix(v,16).ok()){bytes.push(v);i+=3;continue;}}
    bytes.push(input[i]);i+=1;}String::from_utf8_lossy(&bytes).to_string()}
fn images(content:&str)->Vec<String>{let mut result=vec![];for line in content.lines(){let mut remaining=line;
    while let Some(start)=remaining.find("!["){let Some(close)=remaining[start+2..].find("](")else{break;};let reference=start+2+close+2;
        let Some(end)=remaining[reference..].find(')')else{break;};result.push(remaining[reference..reference+end].to_string());remaining=&remaining[reference+end+1..];}}
    result
}
pub fn inventory_notes(root:&Path)->AnalyticsResult<Value>{
    if !root.is_dir(){return Err("Notion export directory does not exist".into());}let root=root.canonicalize().map_err(|e|e.to_string())?;
    let mut paths=vec![];files(&root,&mut paths)?;paths.sort();if paths.is_empty(){return Err("No Markdown pages in export".into());}
    let mut pages=vec![];let mut external=BTreeSet::new();
    for path in paths{if fs::metadata(&path).map_err(|e|e.to_string())?.len()>16*1024*1024{return Err("note page exceeds size bound".into());}
        let bytes=fs::read(&path).map_err(|e|e.to_string())?;let content=std::str::from_utf8(&bytes).map_err(|_|"notes must be UTF-8")?.trim_start_matches('\u{feff}');
        let mut local=vec![];let mut external_count=0;
        for reference in images(content){let reference=reference.trim_matches(['<','>']);if reference.starts_with("https://")||reference.starts_with("http://"){external.insert(reference.to_string());external_count+=1;}
            else{let asset=path.parent().ok_or("note parent missing")?.join(unquote(reference));let exists=asset.canonicalize().ok().is_some_and(|asset|asset.starts_with(&root)&&asset.is_file());local.push(json!({"reference":reference,"exists":exists}));}}
        let title=content.lines().find(|line|!line.trim().is_empty()).map(|line|line.trim_start_matches(['#',' ']).trim().to_string()).unwrap_or_else(||path.file_stem().unwrap_or_default().to_string_lossy().to_string());
        pages.push(json!({"path":path.strip_prefix(&root).map_err(|e|e.to_string())?.to_string_lossy().replace('\\',"/"),"title":title,
            "sha256":format!("{:x}",Sha256::digest(&bytes)),"lines":content.lines().count(),"empty_body":content.lines().skip(1).all(|line|line.trim().is_empty()),
            "local_images":local,"external_image_references":external_count}));
    }
    Ok(json!({"page_count":pages.len(),"pages":pages,"external_images":external.into_iter().map(|url|json!({"url":url,"status":"not_visually_verified"})).collect::<Vec<_>>(),
        "note":"Inventory checks files, not comprehension. Human-readable coverage is in docs/notion_review.md."}))
}
/// 只按 train 的既有 Sharpe 选候选，diagnostic 不参与选择。
pub fn research_gate(variant_names:&[String],results:&BTreeMap<String,Value>)->AnalyticsResult<Value>{
    let mut selected:Option<(&String,f64)>=None;
    for name in variant_names{let rows=results.get(name).ok_or("variant results missing")?;
        if number(&rows["train"]["trades"]).ok_or("training trades missing")?<20.0{continue;}
        if let Some(sharpe)=number(&rows["train"]["sharpe"]){if selected.is_none_or(|(_,old)|sharpe>old){selected=Some((name,sharpe));}}}
    let mut checks=json!({"training_candidate_exists":selected.is_some()});
    if let Some((name,_))=selected{let rows=&results[name];let metric=|period:&str,key:&str|number(&rows[period][key]).ok_or_else(||format!("{name} {period} {key} missing"));
        super::merge(&mut checks,&json!({"positive_train":metric("train","total_return")?>0.0,"positive_validation":metric("validation","total_return")?>0.0,
            "positive_diagnostic":metric("diagnostic","total_return")?>0.0,"sufficient_trades":metric("diagnostic","trades")?>=30.0,
            "sharpe_at_least_half":number(&rows["diagnostic"]["sharpe"]).unwrap_or(0.0)>=0.5,"drawdown_below_20pct":metric("diagnostic","max_drawdown")?>=-0.2,
            "positive_cost_stress":metric("cost_2x","total_return")?>0.0}));}
    let passed=checks.as_object().is_some_and(|m|m.values().all(|v|v==true));
    Ok(json!({"selected_by_train":selected.map(|(name,_)|name),"research_gate":{"passed":passed,"checks":checks}}))
}
pub fn assemble_report(mut disclosed_inputs:Value,variant_names:&[String],results:&BTreeMap<String,Value>,inventory:&Value,output:Option<&Path>)->AnalyticsResult<Value>{
    if !disclosed_inputs.is_object(){return Err("research report inputs must be an object".into());}
    super::merge(&mut disclosed_inputs,&research_gate(variant_names,results)?);
    disclosed_inputs["kind"]=json!("exploratory_not_fresh_holdout");disclosed_inputs["production_ready"]=json!(false);disclosed_inputs["results"]=json!(results);
    disclosed_inputs["note_page_count"]=inventory["page_count"].clone();disclosed_inputs["unverified_external_images"]=json!(inventory["external_images"].as_array().map_or(0,Vec::len));
    if disclosed_inputs.get("engineering_tests").is_none(){disclosed_inputs["engineering_tests"]=json!({"status":"not_run_by_this_command"});}
    if let Some(output)=output{research::report_files(output,&disclosed_inputs)?;}Ok(disclosed_inputs)
}
