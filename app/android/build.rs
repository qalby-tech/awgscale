//! Hands the app's Gradle build this crate's Android library (android/), the way a Tauri plugin does.
fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("android") {
        let library = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("android");
        println!("cargo:android_library_path={}", library.display());
    }
    println!("cargo:rerun-if-changed=build.rs");
}
