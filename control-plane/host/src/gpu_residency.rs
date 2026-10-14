//! Read-only Windows WDDM memory inventory used before Ollama model admission.
//!
//! DXGI supplies each hardware adapter's usable dedicated-memory capacity;
//! PDH supplies system-wide dedicated usage and per-process allocations.

use serde::Serialize;
use std::collections::BTreeMap;
use windows::Win32::Graphics::Dxgi::{CreateDXGIFactory1, DXGI_ADAPTER_FLAG_SOFTWARE, IDXGIFactory1};
use windows_sys::Win32::System::Performance::*;

#[derive(Clone, Debug, Serialize)]
pub struct ProcessAllocation {
    pub pid: u32,
    pub bytes: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct GpuMemorySnapshot {
    pub adapters: Vec<String>,
    pub total_bytes: u64,
    pub used_bytes: u64,
    pub free_bytes: u64,
    pub process_allocations: Vec<ProcessAllocation>,
}

struct Query(usize);

impl Drop for Query {
    fn drop(&mut self) {
        unsafe { PdhCloseQuery(self.0 as _); }
    }
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn pdh_error(code: u32) -> String {
    format!("Windows GPU memory counter error 0x{code:08x}")
}

fn add_counter(query: &Query, path: &str) -> Result<usize, String> {
    let mut counter = std::ptr::null_mut();
    let code = unsafe { PdhAddEnglishCounterW(query.0 as _, wide(path).as_ptr(), 0, &mut counter) };
    if code == 0 { Ok(counter as usize) } else { Err(pdh_error(code)) }
}

fn counter_rows(counter: usize) -> Result<Vec<(String, u64)>, String> {
    for _ in 0..3 {
        let (mut bytes, mut count) = (0u32, 0u32);
        let code = unsafe {
            PdhGetFormattedCounterArrayW(
                counter as _, PDH_FMT_LARGE, &mut bytes, &mut count, std::ptr::null_mut(),
            )
        };
        if code == 0 && bytes == 0 { return Ok(Vec::new()); }
        if code != PDH_MORE_DATA { return Err(pdh_error(code)); }
        if bytes > 32 * 1024 * 1024 { return Err("GPU counter array exceeded safety bound".into()); }
        let mut buffer = vec![0u64; (bytes as usize).div_ceil(8)];
        let ptr = buffer.as_mut_ptr().cast::<PDH_FMT_COUNTERVALUE_ITEM_W>();
        let code = unsafe {
            PdhGetFormattedCounterArrayW(counter as _, PDH_FMT_LARGE, &mut bytes, &mut count, ptr)
        };
        if code == PDH_MORE_DATA { continue; }
        if code != 0 { return Err(pdh_error(code)); }
        let mut rows = Vec::new();
        // SAFETY: PDH populated `count` entries and their UTF-16 names in this
        // aligned buffer; both remain live until all values have been copied.
        unsafe {
            for item in std::slice::from_raw_parts(ptr, count as usize) {
                let mut length = 0;
                while *item.szName.add(length) != 0 { length += 1; }
                let name = String::from_utf16_lossy(std::slice::from_raw_parts(item.szName, length));
                if item.FmtValue.CStatus > 1 { return Err(pdh_error(item.FmtValue.CStatus)); }
                rows.push((name, item.FmtValue.Anonymous.largeValue.max(0) as u64));
            }
        }
        return Ok(rows);
    }
    Err("GPU counter instances changed during enumeration".into())
}

fn adapter_key(instance: &str) -> Option<String> {
    let tail = instance.split_once("luid_")?.1;
    let words: Vec<_> = tail.split('_').take(2).collect();
    (words.len() == 2).then(|| format!("luid_{}_{}", words[0], words[1]).to_ascii_lowercase())
}

/// Capture aggregate dedicated memory plus per-process slices for hardware adapters.
pub fn sample() -> Result<GpuMemorySnapshot, String> {
    let factory: IDXGIFactory1 = unsafe { CreateDXGIFactory1() }.map_err(|error| format!("DXGI: {error}"))?;
    let query_handle = unsafe {
        let mut handle = std::ptr::null_mut();
        let code = PdhOpenQueryW(std::ptr::null(), 0, &mut handle);
        if code != 0 { return Err(pdh_error(code)); }
        handle
    };
    let query = Query(query_handle as usize);
    let adapter_memory = add_counter(&query, r"\GPU Adapter Memory(*)\Dedicated Usage")?;
    let process_memory = add_counter(&query, r"\GPU Process Memory(*)\Dedicated Usage")?;
    let code = unsafe { PdhCollectQueryData(query.0 as _) };
    if code != 0 { return Err(pdh_error(code)); }
    let adapter_rows = counter_rows(adapter_memory)?;
    let process_rows = counter_rows(process_memory)?;

    let mut capacities = BTreeMap::<String, (String, u64)>::new();
    let mut index = 0;
    loop {
        let adapter = match unsafe { factory.EnumAdapters1(index) } {
            Ok(adapter) => adapter,
            Err(error) if error.code() == windows::Win32::Graphics::Dxgi::DXGI_ERROR_NOT_FOUND => break,
            Err(error) => return Err(format!("DXGI adapter enumeration: {error}")),
        };
        index += 1;
        let desc = unsafe { adapter.GetDesc1() }.map_err(|error| format!("DXGI adapter descriptor: {error}"))?;
        if desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE.0 as u32 != 0 { continue; }
        let key = format!("luid_0x{:08x}_0x{:08x}", desc.AdapterLuid.HighPart as u32, desc.AdapterLuid.LowPart).to_ascii_lowercase();
        let name_end = desc.Description.iter().position(|unit| *unit == 0).unwrap_or(desc.Description.len());
        let name = String::from_utf16_lossy(&desc.Description[..name_end]);
        capacities.insert(key, (name, desc.DedicatedVideoMemory as u64));
    }
    if capacities.is_empty() { return Err("DXGI found no hardware GPU adapters".into()); }

    let mut total_bytes = 0u64;
    let mut used_bytes = 0u64;
    let mut adapters = Vec::new();
    for (key, (name, capacity)) in &capacities {
        total_bytes = total_bytes.saturating_add(*capacity);
        adapters.push(name.clone());
        let rows: Vec<_> = adapter_rows.iter().filter(|(instance, _)| adapter_key(instance).as_deref() == Some(key.as_str())).collect();
        if rows.is_empty() { return Err(format!("No WDDM dedicated-memory readings for adapter {name}")); }
        used_bytes = used_bytes.saturating_add(rows.iter().map(|(_, bytes)| *bytes).sum::<u64>());
    }
    if total_bytes == 0 { return Err("DXGI reported no dedicated GPU memory capacity".into()); }

    let mut by_pid = BTreeMap::<u32, u64>::new();
    for (instance, bytes) in process_rows {
        let Some((prefix, rest)) = instance.split_once("_luid_") else { continue; };
        let key = format!("luid_{}", rest.split('_').take(2).collect::<Vec<_>>().join("_")).to_ascii_lowercase();
        if !capacities.contains_key(&key) { continue; }
        let Some(pid) = prefix.strip_prefix("pid_").and_then(|value| value.parse::<u32>().ok()) else { continue; };
        let allocation = by_pid.entry(pid).or_default();
        *allocation = allocation.saturating_add(bytes);
    }
    if by_pid.is_empty() && used_bytes > 0 {
        return Err("WDDM reports GPU allocations but exposes no per-process allocation identities".into());
    }
    let process_allocations = by_pid.into_iter().map(|(pid, bytes)| ProcessAllocation { pid, bytes }).collect();
    Ok(GpuMemorySnapshot {
        adapters, total_bytes, used_bytes: used_bytes.min(total_bytes),
        free_bytes: total_bytes.saturating_sub(used_bytes), process_allocations,
    })
}
