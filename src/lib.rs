//! Shared plumbing for the Ryzen Curve Optimizer root helper.
//!
//! The helper follows one contract: one bounded JSON request on stdin, one JSON line
//! on stdout, exit 0 on success / 1 on failure. Nothing from argv or the environment
//! is ever trusted.

use serde_json::Value;
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, MetadataExt};
use std::path::Path;

pub mod pmtable;
pub mod ryzen_mon;
pub mod smu;
pub mod stream;

/// The first processor block of /proc/cpuinfo (vendor, family, model, name are
/// identical across CPUs), read once per process. The whole file is one
/// seq_file record per CPU and each record samples that CPU's current
/// frequency — on a 32-thread part a full read is ~40 KiB of kernel work that
/// can IPI idle cores awake.
pub fn cpuinfo_head() -> &'static str {
    static HEAD: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    HEAD.get_or_init(|| {
        let Ok(mut f) = std::fs::File::open("/proc/cpuinfo") else { return String::new() };
        let mut buf = Vec::with_capacity(4096);
        let mut chunk = [0u8; 2048];
        loop {
            match f.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => buf.extend_from_slice(&chunk[..n]),
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
            if let Some(end) = buf.windows(2).position(|w| w == b"\n\n") { buf.truncate(end + 1); break; }
            if buf.len() >= 64 * 1024 { break; }
        }
        String::from_utf8_lossy(&buf).into_owned()
    })
}

/// Process-wide setup the root helper runs first.
///
/// pkexec passes the caller's umask through untouched: a user who runs
/// `umask 000; pkexec <helper>` would otherwise get world-writable
/// directories and files created by root (e.g. /run/ryzen-curve-optimizer).
/// Also disables core dumps, so a crash never leaves root memory in a file
/// some other tool might pick up.
pub fn init() {
    unsafe {
        libc::umask(0o022);
        let zero = libc::rlimit { rlim_cur: 0, rlim_max: 0 };
        libc::setrlimit(libc::RLIMIT_CORE, &zero);
    }
}

/// Writes one JSON line to stdout and flushes. Serialized first and handed over in one piece.
pub fn emit(v: &Value) {
    let mut out = io::stdout().lock();
    match serde_json::to_vec(v) {
        Ok(mut buf) => { buf.push(b'\n'); let _ = out.write_all(&buf); }
        Err(_) => { let _ = writeln!(out, "{v}"); }
    }
    let _ = out.flush();
}

/// Emits and converts `ok` into a process exit code.
pub fn finish(v: Value) -> i32 {
    emit(&v);
    if v.get("ok").and_then(Value::as_bool).unwrap_or(false) { 0 } else { 1 }
}

/// Creates (or verifies) a root-owned, non-symlink directory that is not
/// group/other-writable. Mode is set explicitly, never left to the umask.
pub fn secure_dir(p: &str) -> Result<(), String> {
    match std::fs::DirBuilder::new().mode(0o755).create(p) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(format!("{p}: {e}")),
    }
    let m = std::fs::symlink_metadata(p).map_err(|e| format!("{p}: {e}"))?;
    if !m.is_dir() || m.uid() != 0 || m.mode() & 0o022 != 0 {
        return Err(format!("{p} is not a root-owned private directory; refusing to use it"));
    }
    Ok(())
}

/// True if `path` and every directory above it is owned by root and not
/// group/other-writable (a sticky world-writable dir like /tmp fails too),
/// and the file itself carries no setuid/setgid bit. Used before exec'ing a
/// third-party binary as root.
pub fn trusted_path(path: &Path) -> bool {
    let Ok(real) = std::fs::canonicalize(path) else { return false };
    let Ok(md) = std::fs::metadata(&real) else { return false };
    if !md.is_file() || md.uid() != 0 || md.mode() & 0o6022 != 0 { return false; }
    let mut dir = real.parent();
    while let Some(d) = dir {
        match std::fs::metadata(d) {
            Ok(m) if m.is_dir() && m.uid() == 0 && m.mode() & 0o022 == 0 => {}
            _ => return false,
        }
        dir = d.parent();
    }
    true
}

// ── kernel modules ─────────────────────────────────────────────────────────

/// True if the running kernel ships `module` (loadable or built in). Unknown
/// (no modules.dep) counts as present so that modprobe still gets its chance.
fn module_installed(module: &str) -> bool {
    let norm = |s: &str| s.replace('-', "_");
    let want = norm(module);
    let mut u: libc::utsname = unsafe { std::mem::zeroed() };
    if unsafe { libc::uname(&mut u) } != 0 { return true; }
    let rel = unsafe { std::ffi::CStr::from_ptr(u.release.as_ptr()) }.to_string_lossy().into_owned();
    let base = Path::new("/lib/modules").join(rel);
    let Ok(dep) = std::fs::read_to_string(base.join("modules.dep")) else { return true };
    let builtin = std::fs::read_to_string(base.join("modules.builtin")).unwrap_or_default();
    dep.lines().map(|l| l.split(':').next().unwrap_or("")).chain(builtin.lines()).any(|path| {
        let file = path.rsplit('/').next().unwrap_or("");
        file.find(".ko").map_or(false, |i| norm(&file[..i]) == want)
    })
}

/// Loads a kernel module at most once per process, and only when the running
/// kernel actually has it. Returns true if the module is loaded afterwards (or was already).
pub fn modprobe(module: &str) -> bool {
    // Callers pass fixed names. Reject anything modprobe could treat as an option or a path.
    if module.is_empty() || module.len() > 64 || module.starts_with('-')
        || !module.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return false;
    }
    static TRIED: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());
    let loaded = || Path::new("/sys/module").join(module.replace('-', "_")).exists();
    if loaded() { return true; }
    {
        let mut t = TRIED.lock().unwrap_or_else(|e| e.into_inner());
        if t.iter().any(|m| m == module) { return false; }
        t.push(module.to_owned());
    }
    if !module_installed(module) { return false; }
    for p in ["/sbin/modprobe", "/usr/sbin/modprobe", "/usr/bin/modprobe", "/bin/modprobe"] {
        if trusted_path(Path::new(p)) {
            let _ = std::process::Command::new(p).arg(module).env_clear()
                .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
                .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null()).status();
            return loaded();
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn module_lookup_is_quiet() {
        assert!(!modprobe("rco_no_such_module_xyz"));
        assert!(!modprobe("rco_no_such_module_xyz"));  // second call: no second spawn
        assert!(!modprobe("-c") && !modprobe("../acpi_call") && !modprobe("acpi call"));
    }
    #[test]
    fn trusted_path_rejects_tmp() {
        use std::os::unix::fs::PermissionsExt;
        // Do not assume /tmp is 1777: some build sandboxes ship a private root-owned temp.
        let base = std::env::temp_dir().join(format!("rco-trustdir-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir(&base).unwrap();
        std::fs::set_permissions(&base, std::fs::Permissions::from_mode(0o1777)).unwrap();
        let p = base.join("x");
        std::fs::write(&p, b"x").unwrap();
        assert!(!trusted_path(&p), "a world-writable parent must never be trusted");
        std::fs::remove_dir_all(&base).unwrap();
        assert!(!trusted_path(Path::new("/nonexistent/rco")));
    }
}
