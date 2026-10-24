use super::{StoreResult,StoreError,absolute_path,fingerprint,write_json,snapshot_sqlite,require};
use serde::{Serialize,Deserialize};
use std::{collections::{BTreeMap,BTreeSet},path::{Path,PathBuf}};
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct BackupManifest{pub schema:u8,pub files:BTreeMap<String,String>,pub scope:String,pub authenticity:String}
#[derive(Debug,Clone,Serialize,Deserialize)]
pub struct RestoreReport{pub restored_files:usize,pub destination:PathBuf,pub verified:bool}
fn linked(metadata:&std::fs::Metadata)->bool{
    if metadata.file_type().is_symlink(){return true;}
    #[cfg(windows)] {use std::os::windows::fs::MetadataExt;if metadata.file_attributes()&0x400!=0{return true;}}
    false
}
pub fn directory_files(root:&Path)->StoreResult<Vec<PathBuf>>{
    fn walk(path:&Path,files:&mut Vec<PathBuf>)->StoreResult<()>{for entry in std::fs::read_dir(path)?{let path=entry?.path();let metadata=std::fs::symlink_metadata(&path)?;
        require(!linked(&metadata),"linked directory members are not supported")?;if metadata.is_dir(){walk(&path,files)?;}else if metadata.is_file(){files.push(path);}else{return Err(StoreError("special directory members are not supported".into()));}}
        Ok(())}
    let mut files=vec![];walk(root,&mut files)?;files.sort();Ok(files)
}
pub fn safe_member(base:&Path,relative:&str)->StoreResult<PathBuf>{
    require(!relative.is_empty()&&!relative.starts_with('/')&&!relative.contains(['\\',':'])&&relative.split('/').all(|s|!s.is_empty()&&s!="."&&s!=".."),"unsafe bundle member path")?;
    for part in relative.split('/'){
        let stem=part.split('.').next().unwrap_or("").to_ascii_uppercase();require(!["CON","PRN","AUX","NUL"].contains(&stem.as_str())
            &&!(stem.len()==4&&(stem.starts_with("COM")||stem.starts_with("LPT"))&&matches!(stem.as_bytes()[3],b'1'..=b'9')),"reserved device member")?;
    }
    let root=absolute_path(base)?;let target=absolute_path(&root.join(relative))?;require(target.starts_with(&root)&&target!=root,"bundle member escapes root")?;Ok(target)
}
fn relative(root:&Path,path:&Path)->StoreResult<String>{Ok(path.strip_prefix(root).map_err(|e|StoreError(e.to_string()))?.to_string_lossy().replace('\\',"/"))}
fn wal_frames(path:&Path)->bool{std::fs::metadata(PathBuf::from(format!("{}-wal",path.to_string_lossy()))).is_ok_and(|m|m.len()>0)}
pub fn verify_bundle(bundle:&Path)->StoreResult<BackupManifest>{
    let root=bundle.canonicalize()?;let manifest:BackupManifest=crate::indexed_json::from_slice(&std::fs::read(root.join("manifest.json"))?)?;
    require(manifest.schema==1&&!manifest.files.is_empty(),"nonempty versioned bundle manifest required")?;let payload=root.join("payload");
    let actual: BTreeSet<_>=directory_files(&payload)?.iter().map(|p|relative(&payload,p)).collect::<StoreResult<_>>()?;
    require(actual==manifest.files.keys().cloned().collect(),"unregistered bundle members")?;
    for(name,digest)in &manifest.files{let path=safe_member(&payload,name)?;require(path.is_file()&&fingerprint(&path)?==*digest,"bundle integrity failure")?;}Ok(manifest)
}
pub fn freeze_file(source:&Path,destination:&Path)->StoreResult<String>{let before=fingerprint(source)?;
    if let Some(parent)=destination.parent(){std::fs::create_dir_all(parent)?;}std::fs::copy(source,destination)?;
    require(fingerprint(source)?==before&&fingerprint(destination)?==before,"input changed during snapshot")?;Ok(before)}
pub fn create_bundle(source:&Path,destination:&Path)->StoreResult<BackupManifest>{
    let source=source.canonicalize()?;let destination=absolute_path(destination)?;
    require(source.is_dir()&&!destination.exists(),"existing source and new backup destination required")?;
    require(!destination.starts_with(&source)&&!source.starts_with(&destination),"backup and source must be disjoint trees")?;
    let members:Vec<_>=directory_files(&source)?.into_iter().filter(|p|{let name=p.file_name().map(|n|n.to_string_lossy()).unwrap_or_default();
        name!=".operations.lock"&&!name.ends_with("-wal")&&!name.ends_with("-shm")&&!name.ends_with("-journal")}).collect();
    require(!members.is_empty(),"nothing to back up")?;if let Some(parent)=destination.parent(){std::fs::create_dir_all(parent)?;}std::fs::create_dir(&destination)?;
    let mut files=BTreeMap::new();for p in members{let name=relative(&source,&p)?;let target=safe_member(&destination.join("payload"),&name)?;
        if p.extension().is_some_and(|s|s=="sqlite"){
            let before=fingerprint(&p)?;let checkpointed=!wal_frames(&p);snapshot_sqlite(&p,&target)?;
            if checkpointed&&!wal_frames(&p)&&fingerprint(&p)?==before{std::fs::copy(&p,&target)?;
                require(fingerprint(&p)?==before&&fingerprint(&target)?==before&&!wal_frames(&p),"checkpointed source changed during backup")?;}
        }else{freeze_file(&p,&target)?;}
        files.insert(name,fingerprint(&target)?);
    }
    let manifest=BackupManifest{schema:1,files,scope:"local directory snapshot; writers must be quiescent".into(),authenticity:"hashes detect damage, not malicious replacement of payload and manifest".into()};
    write_json(&destination.join("manifest.json"),&manifest)?;verify_bundle(&destination)
}
pub fn restore_bundle(bundle:&Path,destination:&Path)->StoreResult<RestoreReport>{
    let bundle=bundle.canonicalize()?;let manifest=verify_bundle(&bundle)?;let destination=absolute_path(destination)?;
    require(!destination.exists()&&!destination.starts_with(&bundle),"restore requires a new destination outside bundle")?;
    if let Some(parent)=destination.parent(){std::fs::create_dir_all(parent)?;}std::fs::create_dir(&destination)?;
    for(name,digest)in &manifest.files{let target=safe_member(&destination,name)?;let source=safe_member(&bundle.join("payload"),name)?;
        if let Some(parent)=target.parent(){std::fs::create_dir_all(parent)?;}std::fs::copy(source,&target)?;require(fingerprint(&target)?==*digest,"restore verification failed")?;}
    Ok(RestoreReport{restored_files:manifest.files.len(),destination,verified:true})
}
