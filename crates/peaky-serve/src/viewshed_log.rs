//! Console progress for viewshed warm (stderr; always on for step milestones).

use std::io::Write;

pub fn viewshed_progress_log(msg: &str) {
    eprintln!("[peaky] viewshed {msg}");
    let _ = std::io::stderr().flush();
}

pub fn viewshed_progress_log_verbose(verbose: bool, msg: &str) {
    if verbose {
        viewshed_progress_log(msg);
    }
}
