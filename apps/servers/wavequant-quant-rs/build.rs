use std::{env, fs, io, path::{Path, PathBuf}};
use sha2::{Digest, Sha256};

fn sources(directory: &Path, files: &mut Vec<PathBuf>) -> io::Result<()> {
    println!("cargo:rerun-if-changed={}", directory.display());
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_symlink() { return Err(io::Error::new(io::ErrorKind::InvalidInput, "engine source cannot be a symbolic link")); }
        if kind.is_dir() { sources(&entry.path(), files)?; } else if kind.is_file() { files.push(entry.path()); }
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR")?);
    let root = manifest.ancestors().nth(3).ok_or("Cargo workspace root missing")?;
    let mut files = Vec::new(); sources(&manifest.join("src"), &mut files)?;
    files.extend([manifest.join("Cargo.toml"), manifest.join("build.rs"), root.join("Cargo.toml"), root.join("Cargo.lock"), root.join("rust-toolchain.toml")]);
    files.push(manifest.join("scripts/rust_sdk_worker.py"));
    files.sort(); files.dedup();
    let mut hash = Sha256::new(); hash.update(b"wavequant-native-engine-v1\0");
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        let name = path.strip_prefix(root)?.to_str().ok_or("engine path is not UTF-8")?.replace('\\', "/");
        let bytes = fs::read(path)?;
        hash.update((name.len() as u64).to_le_bytes()); hash.update(name.as_bytes());
        hash.update((bytes.len() as u64).to_le_bytes()); hash.update(bytes);
    }
    fs::write(PathBuf::from(env::var("OUT_DIR")?).join("quant-engine.sha256"), format!("{:x}", hash.finalize()))?;
    Ok(())
}
