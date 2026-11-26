//! health-reader: read-only kernel-log reader for CO Search (run through pkexec, silent polkit action).
//!
//! Request (one JSON object on stdin):
//!   {"since": <last_seq>}   classified kernel events after that sequence number (default),
//!   {"op": "boots"}         this boot's id; the boot history is not available here.
//! It reads /dev/kmsg only and writes nothing.

use ryzen_curve_optimizer_helper::{boot_id, finish, health, init, stream, watchdog};
use serde_json::{json, Value};

const MAX_STDIN_BYTES: usize = 4096;

/// `boots`: the current boot only. Without a journal reader the previous boots are unknown, which
/// the GUI treats as "cannot confirm that the previous boot crashed" (it then ignores the machine
/// checks from this boot's start instead of guessing).
fn boots() -> Value {
    let current = boot_id();
    if current.is_empty() { return json!({"ok": false, "error": "/proc/sys/kernel/random/boot_id unreadable"}); }
    json!({"ok": true, "current": current, "boots": [{"boot": current}], "pstore": {"entries": []}})
}

fn run() -> Value {
    let req = match stream::read_request_line(MAX_STDIN_BYTES) { Ok(v) => v, Err(e) => return e };
    let Some(obj) = req.as_object() else { return json!({"ok": false, "error": "payload must be a JSON object"}); };
    watchdog(30);
    match obj.get("op").and_then(Value::as_str) {
        Some("boots") => boots(),
        None => match obj.get("since") {
            None | Some(Value::Null) => health::scan(0),
            // Sequence numbers are integers; the GUI sends them as JSON numbers (possibly "12.0").
            Some(v) => match v.as_f64().filter(|f| f.is_finite() && *f >= 0.0 && *f < 1.8e19) {
                Some(f) => health::scan(f as u64),
                None => json!({"ok": false, "error": "since must be a non-negative number"}),
            },
        },
        Some(other) => json!({"ok": false, "error": format!("unknown op: {other}")}),
    }
}

fn main() {
    init();
    let v = run();
    std::process::exit(finish(v));
}
