//! Embed the mod data the configurator reads (the original tables, BG1 name
//! maps, Item Randomiser's texts in every language, the mod's stand-in items
//! and the editor UI), so the library and the standalone executable never
//! depend on a source checkout.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const FIXED: &[&str] = &[
    "lib/fl#bg1pal.tpa",
    "copy/flbhcryp.itm",
    "copy/flrbelt1.itm",
    "copy/ward.itm",
    "copy/flrward1.bam",
    "tools/flir_config/ui/index.html",
    "tools/flir_config/ui/app.js",
    "tools/flir_config/ui/style.css",
];

fn walk(root: &Path, relative: &str, out: &mut Vec<String>) {
    let directory = root.join(relative);
    println!("cargo:rerun-if-changed={}", directory.display());
    let mut entries: Vec<_> = fs::read_dir(&directory)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", directory.display()))
        .map(|entry| entry.expect("directory entry"))
        .collect();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry.file_name().to_string_lossy().into_owned();
        let child = format!("{relative}/{name}");
        let kind = entry.file_type().expect("file type");
        if kind.is_dir() {
            walk(root, &child, out);
        } else if name.to_ascii_lowercase().ends_with(".2da") {
            out.push(child);
        }
    }
}

/// Every file and folder below `relative`, except the folders named `skip`.
fn walk_tree(root: &Path, relative: &str, skip: &str, files: &mut Vec<String>, folders: &mut Vec<String>) {
    let directory = root.join(relative);
    println!("cargo:rerun-if-changed={}", directory.display());
    folders.push(relative.to_string());
    let mut entries: Vec<_> = fs::read_dir(&directory)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", directory.display()))
        .map(|entry| entry.expect("directory entry"))
        .collect();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let name = entry.file_name().to_string_lossy().into_owned();
        let child = format!("{relative}/{name}");
        if entry.file_type().expect("file type").is_dir() {
            if name != skip {
                walk_tree(root, &child, skip, files, folders);
            }
        } else {
            files.push(child);
        }
    }
}

fn main() {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let root = manifest.join("..").join("..");
    let mut files = Vec::new();
    walk(&root, "lists", &mut files);
    // Every translation as written (WeiDU's iconv tools are not mod data).
    let mut folders = Vec::new();
    walk_tree(&root, "languages", "iconv", &mut files, &mut folders);
    files.extend(FIXED.iter().map(|name| name.to_string()));
    files.sort();
    folders.sort();
    let mut source = String::from("pub static FILES: &[(&str, &[u8])] = &[\n");
    for name in &files {
        let path = root.join(name);
        let path = path
            .canonicalize()
            .unwrap_or_else(|error| panic!("missing mod data file {}: {error}", path.display()));
        println!("cargo:rerun-if-changed={}", path.display());
        source.push_str(&format!(
            "    ({:?}, include_bytes!({:?})),\n",
            name,
            path.to_string_lossy()
        ));
    }
    source.push_str("];\npub static FOLDERS: &[&str] = &[\n");
    for name in &folders {
        source.push_str(&format!("    {name:?},\n"));
    }
    source.push_str("];\n");
    let out = PathBuf::from(env::var("OUT_DIR").expect("out dir")).join("embedded.rs");
    fs::write(out, source).expect("write embedded.rs");
}
