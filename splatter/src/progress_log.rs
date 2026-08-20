//! stderr progress helpers for long inner loops.

use std::io::{self, Write};
use std::time::Instant;

pub fn row_progress(label: &str, iy: usize, n: usize, enabled: bool) {
    if !enabled {
        return;
    }
    let row_every = row_every_for(n);
    if iy % row_every != 0 && iy + 1 != n {
        return;
    }
    emit_row(label, iy, n, None);
}

/// Row loop progress with time-based heartbeats (default 5s) so long rows don't look frozen.
pub struct RowProgress {
    label: String,
    n: usize,
    enabled: bool,
    row_every: usize,
    last_log: Instant,
    heartbeat_secs: f64,
}

impl RowProgress {
    pub fn new(label: impl Into<String>, n: usize, enabled: bool) -> Self {
        Self {
            label: label.into(),
            n,
            enabled,
            row_every: row_every_for(n),
            last_log: Instant::now(),
            heartbeat_secs: 5.0,
        }
    }

    pub fn tick(&mut self, iy: usize) {
        self.tick_with_extra(iy, None);
    }

    pub fn tick_with_extra(&mut self, iy: usize, extra: Option<&str>) {
        if !self.enabled {
            return;
        }
        let due_row = iy % self.row_every == 0 || iy + 1 == self.n;
        let due_time = self.last_log.elapsed().as_secs_f64() >= self.heartbeat_secs;
        if !due_row && !due_time {
            return;
        }
        emit_row(&self.label, iy, self.n, extra);
        self.last_log = Instant::now();
    }
}

fn row_every_for(n: usize) -> usize {
    if n >= 3000 {
        50
    } else {
        (n / 40).max(25).min(100)
    }
}

fn emit_row(label: &str, iy: usize, n: usize, extra: Option<&str>) {
    let pct = (iy + 1) as f64 / n as f64 * 100.0;
    match extra {
        Some(detail) => {
            let _ = writeln!(
                io::stderr(),
                "[splatter] [progress] {label} row {iy}/{n} ({pct:.0}%) {detail}"
            );
        }
        None => {
            let _ = writeln!(
                io::stderr(),
                "[splatter] [progress] {label} row {iy}/{n} ({pct:.0}%)"
            );
        }
    }
    let _ = io::stderr().flush();
}

pub fn phase(label: &str, detail: &str, enabled: bool) {
    if !enabled {
        return;
    }
    let _ = writeln!(io::stderr(), "[splatter] [phase] {label} {detail}");
    let _ = io::stderr().flush();
}

pub fn loop_log(enabled: bool, msg: &str) {
    if !enabled {
        return;
    }
    let _ = writeln!(io::stderr(), "[splatter] [progress] {msg}");
    let _ = io::stderr().flush();
}

pub fn col_log_every(n: usize) -> usize {
    if n >= 3000 {
        100
    } else {
        (n / 20).max(50)
    }
}

pub fn progress_enabled(verbose: bool) -> bool {
    verbose || std::env::var("PEAKY_PEAK_SCAN_LOG").ok().as_deref() == Some("1")
}
