//! Startup-only, append-only trace. File failures never affect the host.
use std::{env, fs::{self, OpenOptions}, io::Write, path::PathBuf,
    sync::{Mutex, OnceLock}, time::{Instant, SystemTime, UNIX_EPOCH}};

struct Trace { origin: Instant, run_id: String, path: PathBuf, lock: Mutex<()> }
static TRACE: OnceLock<Trace> = OnceLock::new();

pub fn init(origin: Instant) {
    let wall = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
    let run_id = format!("{}-{}", wall.as_nanos(), std::process::id());
    let base = env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(env::temp_dir);
    let path = base.join("Ariadne").join("startup").join(&run_id).join("host.jsonl");
    let _ = TRACE.set(Trace { origin, run_id, path, lock: Mutex::new(()) });
    mark("host_start", "started", serde_json::json!({}));
}

pub fn run_id() -> Option<&'static str> { TRACE.get().map(|trace| trace.run_id.as_str()) }

fn row(origin: Instant, run_id: &str, phase: &str, status: &str, detail: serde_json::Value) -> serde_json::Value {
    serde_json::json!({"run_id": run_id, "source": "host", "pid": std::process::id(),
        "wall_time_unix_ms": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64,
        "clock_scope": "host_process", "elapsed_ms": origin.elapsed().as_secs_f64() * 1000.0,
        "phase": phase, "status": status, "detail": detail})
}

pub fn mark(phase: &str, status: &str, detail: serde_json::Value) {
    if let Some(trace) = TRACE.get() {
        if let Ok(_guard) = trace.lock.lock() {
            let value = row(trace.origin, &trace.run_id, phase, status, detail);
            if let Some(parent) = trace.path.parent() {
                if fs::create_dir_all(parent).is_ok() {
                    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&trace.path) {
                        let _ = writeln!(file, "{}", value);
                        let _ = file.flush();
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn records_monotonic_elapsed_and_correlatable_wall_time() {
        let value = super::row(std::time::Instant::now(), "test", "host_start", "started", serde_json::json!({}));
        assert!(value["elapsed_ms"].as_f64().unwrap() >= 0.0);
        assert!(value["wall_time_unix_ms"].as_u64().unwrap() > 0);
        assert_eq!(value["clock_scope"], "host_process");
        assert_eq!(value["run_id"], "test");
    }
}
