use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn find_resource_compiler() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = env::var_os("PATH") {
        candidates.extend(env::split_paths(&path).map(|directory| directory.join("rc.exe")));
    }

    let mut sdk_roots = Vec::new();
    if let Some(root) = env::var_os("WindowsSdkDir") {
        sdk_roots.push(PathBuf::from(root).join("bin"));
    }
    if let Some(root) = env::var_os("ProgramFiles(x86)") {
        sdk_roots.push(PathBuf::from(root).join("Windows Kits").join("10").join("bin"));
    }
    if let Some(root) = env::var_os("ProgramFiles") {
        sdk_roots.push(PathBuf::from(root).join("Windows Kits").join("10").join("bin"));
    }

    for sdk_root in sdk_roots {
        let Ok(version_directories) = fs::read_dir(sdk_root) else {
            continue;
        };
        for version_directory in version_directories.flatten() {
            for architecture in ["x64", "x86", "arm64"] {
                candidates.push(version_directory.path().join(architecture).join("rc.exe"));
            }
        }
    }

    candidates.into_iter().find(|candidate| candidate.is_file())
}

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=ariadne.rc");
    println!("cargo:rerun-if-changed=assets/branding/ariadne.ico");
    println!("cargo:rerun-if-env-changed=PATH");

    if env::var_os("CARGO_CFG_WINDOWS").is_none() {
        return;
    }

    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("build output dir"));
    let resource_file = manifest_dir.join("ariadne.rc");
    let resource_output = out_dir.join("ariadne.res");
    let compiler = match find_resource_compiler() {
        Some(path) => path,
        None => {
            println!("cargo:warning=rc.exe unavailable; building without the optional host icon");
            return;
        }
    };
    let status = match Command::new(&compiler)
        .current_dir(&manifest_dir)
        .args([
            "/nologo".to_string(),
            format!("/fo{}", resource_output.display()),
            resource_file.display().to_string(),
        ])
        .status()
    {
        Ok(status) => status,
        Err(error) => {
            println!("cargo:warning=resource compiler unavailable at {}: {error}", compiler.display());
            return;
        }
    };
    if !status.success() {
        panic!("rc.exe failed with status {status}");
    }

    println!("cargo:rustc-link-arg-bin=ariadne-host={}", resource_output.display());
}
