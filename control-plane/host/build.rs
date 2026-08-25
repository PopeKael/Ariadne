use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=ariadne.rc");
    println!("cargo:rerun-if-changed=assets/branding/ariadne.ico");

    if env::var_os("CARGO_CFG_WINDOWS").is_none() {
        return;
    }

    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("build output dir"));
    let resource_file = manifest_dir.join("ariadne.rc");
    let resource_output = out_dir.join("ariadne.res");
    let status = Command::new("rc.exe")
        .current_dir(&manifest_dir)
        .args([
            "/nologo".to_string(),
            format!("/fo{}", resource_output.display()),
            resource_file.display().to_string(),
        ])
        .status()
        .expect("rc.exe was not found; build through the Visual Studio MSVC environment");
    if !status.success() {
        panic!("rc.exe failed with status {status}");
    }

    println!("cargo:rustc-link-arg-bin=ariadne-host={}", resource_output.display());
}
