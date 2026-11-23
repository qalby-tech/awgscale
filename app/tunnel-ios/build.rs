//! For iOS, the Swift side of the plugin (ios/, a Swift package) is compiled and linked into the app;
//! it needs Tauri's own Swift package next to it, which the `tauri` crate hands over
//! (DEP_TAURI_IOS_LIBRARY_PATH) and which is copied to .tauri/tauri-api. This is what
//! tauri-plugin's build helper does for a plugin; only a Mac has the Swift toolchain for iOS, so on
//! any other machine nothing is built here (a `cargo check` for iOS still type-checks the Rust).

fn main() {
    println!("cargo:rerun-if-changed=ios/Package.swift");
    println!("cargo:rerun-if-changed=ios/Sources");
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os == "ios" {
        #[cfg(target_os = "macos")]
        link_swift();
    }
}

#[cfg(target_os = "macos")]
fn link_swift() {
    use std::path::{Path, PathBuf};

    fn copy(from: &Path, to: &Path) {
        std::fs::create_dir_all(to).expect("cannot create .tauri/tauri-api");
        for entry in std::fs::read_dir(from).expect("cannot read Tauri's Swift package") {
            let entry = entry.expect("cannot read Tauri's Swift package");
            let name = entry.file_name();
            if [".build", "Package.resolved", "Tests"].iter().any(|skipped| name == *skipped) {
                continue;
            }
            let target = to.join(&name);
            if entry.file_type().expect("cannot read Tauri's Swift package").is_dir() {
                copy(&entry.path(), &target);
            } else {
                std::fs::copy(entry.path(), &target).expect("cannot copy Tauri's Swift package");
            }
        }
    }

    let tauri_library = std::env::var("DEP_TAURI_IOS_LIBRARY_PATH").expect("the tauri crate did not say where its Swift package is");
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let copied = manifest.join(".tauri").join("tauri-api");
    let _ = std::fs::remove_dir_all(&copied);
    copy(Path::new(&tauri_library), &copied);
    tauri_utils::build::link_apple_library(&std::env::var("CARGO_PKG_NAME").expect("CARGO_PKG_NAME"), manifest.join("ios"));
}
