//! Embed the mod data the configurator reads (the original tables, BG1 name
//! maps, game texts, the mod's stand-in items and the editor UI), so the
//! library and the standalone executable never depend on a source checkout.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const FIXED: &[&str] = &[
    "lib/fl#bg1pal.tpa",
    "languages/english/in-game.tra",
    "languages/english/ppdjinn.tra",
    "languages/english/hlskull.tra",
    "languages/bgee/english/in-game.tra",
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

fn main() {
    let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let root = manifest.join("..").join("..");
    let mut files = Vec::new();
    walk(&root, "lists", &mut files);
    files.extend(FIXED.iter().map(|name| name.to_string()));
    files.sort();
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
    source.push_str("];\n");
    let out = PathBuf::from(env::var("OUT_DIR").expect("out dir")).join("embedded.rs");
    fs::write(out, source).expect("write embedded.rs");
}
