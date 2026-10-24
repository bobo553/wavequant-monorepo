//! 仅观察本进程或池创建的已知子进程，无法取得时保留未知。
#[cfg(target_os="linux")]
pub fn peak_rss_bytes(pid:u32)->Option<u64>{let text=std::fs::read_to_string(format!("/proc/{pid}/status")).ok()?;text.lines().find_map(|line|line.strip_prefix("VmHWM:").and_then(|v|v.split_whitespace().next()?.parse::<u64>().ok()?.checked_mul(1024)))}
#[cfg(target_os="windows")]
pub fn peak_rss_bytes(pid:u32)->Option<u64>{
    use std::{collections::BTreeMap,sync::Mutex};
    static SAMPLES:Mutex<BTreeMap<u32,(Option<u64>,bool)>>=Mutex::new(BTreeMap::new());
    let mut samples=SAMPLES.lock().ok()?;
    if samples.len()>=32&&!samples.contains_key(&pid){let retired=samples.iter().find(|(_,(_,pending))|!pending).map(|(pid,_)|*pid);if let Some(pid)=retired{samples.remove(&pid);}else{return None;}}
    let sample=samples.entry(pid).or_insert((None,false));let previous=sample.0;
    if !sample.1{sample.1=true;std::thread::spawn(move||{let value=query_peak(pid);if let Ok(mut samples)=SAMPLES.lock(){if let Some(sample)=samples.get_mut(&pid){if let Some(value)=value{sample.0=Some(sample.0.unwrap_or(0).max(value));}sample.1=false;}}});}
    previous
}
/// 诊断采样在后台进行，首个结果允许未知，不阻塞图表发布。
#[cfg(target_os="windows")]
fn query_peak(pid:u32)->Option<u64>{
    use std::{os::windows::process::CommandExt,process::{Command,Stdio},time::{Instant,Duration},thread};
    let mut child=Command::new("powershell.exe").args(["-NoProfile","-NonInteractive","-WindowStyle","Hidden","-Command",&format!("(Get-Process -Id {pid} -ErrorAction Stop).PeakWorkingSet64")]).creation_flags(0x08000000).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let started=Instant::now();loop{match child.try_wait(){Ok(Some(status))=>{if !status.success(){return None;}break;},Ok(None)if started.elapsed()<Duration::from_secs(2)=>thread::sleep(Duration::from_millis(20)),_=>{let _=child.kill();let _=child.wait();return None;}}}
    let output=child.wait_with_output().ok()?;std::str::from_utf8(&output.stdout).ok()?.trim().parse().ok()
}
#[cfg(not(any(target_os="windows",target_os="linux")))]
pub fn peak_rss_bytes(_pid:u32)->Option<u64>{None}