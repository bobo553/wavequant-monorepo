use super::{EventStore,StoreError,StoreResult,fingerprint,require,identifier};
use chrono::{DateTime,Utc};
use crate::indexed_json::{Value,json};
use std::{collections::BTreeMap,path::{Path,PathBuf}};

/// 调用者显式给出运行相关源码、配置和锁文件，不遍历秘密配置或无关目录。
pub fn environment_snapshot(root:&Path,files:&[PathBuf])->StoreResult<Value>{
    let root=root.canonicalize()?;let mut hashes=BTreeMap::new();for relative in files{
        let path=root.join(relative).canonicalize()?;require(path.starts_with(&root),"environment source escapes project")?;
        let name=path.strip_prefix(&root).map_err(|e|StoreError(e.to_string()))?.to_string_lossy().replace('\\',"/");hashes.insert(name,fingerprint(&path)?);
    }
    Ok(json!({"implementation":"rust","package_version":env!("CARGO_PKG_VERSION"),"os":std::env::consts::OS,"arch":std::env::consts::ARCH,"hashes":hashes}))
}
pub struct ExperimentRegistry<'a>{pub store:&'a mut EventStore}
impl ExperimentRegistry<'_>{
    pub fn start(&mut self,run_id:&str,when:DateTime<Utc>,config:Value,inputs:&BTreeMap<String,String>,environment:Value)->StoreResult<()>{
        identifier(run_id)?;require(!inputs.is_empty(),"fingerprinted experiment inputs required")?;
        for(path,digest)in inputs{require(fingerprint(Path::new(path))?==*digest,"experiment input hash mismatch")?;}
        self.store.transaction(|store|{require(store.get(&format!("experiment:{run_id}"))?.is_none(),"run ID already exists; never overwrite history")?;
            store.append(&format!("experiment:{run_id}"),"EXPERIMENT_STARTED",when,json!({"config":config,"inputs":inputs,"environment":environment}))?;Ok(())})
    }
    pub fn finish(&mut self,run_id:&str,when:DateTime<Utc>,artifacts:&[PathBuf],status:&str,error:Option<&str>)->StoreResult<()>{
        require(["COMPLETED","FAILED"].contains(&status),"terminal experiment status required")?;let mut hashes=BTreeMap::new();
        for path in artifacts{hashes.insert(path.canonicalize()?.to_string_lossy().into_owned(),fingerprint(path)?);}
        self.store.transaction(|store|{let started=store.get(&format!("experiment:{run_id}"))?.ok_or_else(||StoreError("unknown experiment".into()))?;
            let start=DateTime::parse_from_rfc3339(&started.event_time).map_err(|e|StoreError(e.to_string()))?;
            require(when>=start.with_timezone(&Utc),"finish precedes start")?;require(store.get(&format!("experiment:end:{run_id}"))?.is_none(),"experiment already terminal")?;
            if status=="COMPLETED"{let inputs=started.payload["inputs"].as_object().ok_or_else(||StoreError("missing experiment inputs".into()))?;
                for(path,digest)in inputs{require(digest.as_str()==Some(fingerprint(Path::new(path))?.as_str()),"input changed during experiment")?;}require(!hashes.is_empty(),"completed run requires artifacts")?;}
            store.append(&format!("experiment:end:{run_id}"),"EXPERIMENT_FINISHED",when,json!({"status":status,"artifacts":hashes,"error":error}))?;Ok(())})
    }
    pub fn verify_artifacts(&self,run_id:&str)->StoreResult<BTreeMap<String,bool>>{
        let event=self.store.get(&format!("experiment:end:{run_id}"))?.ok_or_else(||StoreError("experiment has not finished".into()))?;
        let artifacts=event.payload["artifacts"].as_object().ok_or_else(||StoreError("experiment artifacts unavailable".into()))?;
        Ok(artifacts.iter().map(|(p,d)|(p.clone(),fingerprint(Path::new(p)).ok().as_deref()==d.as_str())).collect())
    }
}
