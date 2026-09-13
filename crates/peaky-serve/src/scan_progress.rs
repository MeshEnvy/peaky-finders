//! In-memory async scan progress for client polling (fortify, alternates, link solver).

use parking_lot::Mutex;
use serde_json::{json, Value};
use std::collections::HashMap;

#[derive(Clone, Debug)]
struct ScanProgress {
    phase: String,
    done: i32,
    total: i32,
    detail: String,
}

struct Inner {
    state: HashMap<String, ScanProgress>,
    outcomes: HashMap<String, Value>,
    active_gen: HashMap<String, u64>,
}

impl Default for Inner {
    fn default() -> Self {
        Self {
            state: HashMap::new(),
            outcomes: HashMap::new(),
            active_gen: HashMap::new(),
        }
    }
}

#[derive(Clone, Default)]
pub struct ScanProgressHub {
    inner: std::sync::Arc<Mutex<Inner>>,
}

impl ScanProgressHub {
    pub fn begin(&self, slug: &str) -> u64 {
        let mut inner = self.inner.lock();
        let gen = inner.active_gen.get(slug).copied().unwrap_or(0) + 1;
        inner.active_gen.insert(slug.to_string(), gen);
        inner.state.remove(slug);
        inner.outcomes.remove(slug);
        gen
    }

    pub fn update(
        &self,
        slug: &str,
        gen: u64,
        phase: &str,
        done: i32,
        total: i32,
        detail: &str,
    ) {
        let mut inner = self.inner.lock();
        if inner.active_gen.get(slug) != Some(&gen) {
            return;
        }
        inner.state.insert(
            slug.to_string(),
            ScanProgress {
                phase: phase.to_string(),
                done,
                total,
                detail: detail.to_string(),
            },
        );
    }

    pub fn active(&self, slug: &str, gen: u64) -> bool {
        self.inner.lock().active_gen.get(slug) == Some(&gen)
    }

    pub fn finish(
        &self,
        slug: &str,
        gen: u64,
        status: &str,
        result: Option<Value>,
        error: Option<&str>,
        error_status: Option<u16>,
    ) {
        let mut inner = self.inner.lock();
        if inner.active_gen.get(slug) != Some(&gen) {
            return;
        }
        inner.state.remove(slug);
        let mut payload = json!({
            "gen": gen,
            "status": status,
            "progress": Value::Null,
            "result": result,
            "error": error,
            "error_status": error_status,
        });
        inner.outcomes.insert(slug.to_string(), payload.take());
    }

    pub fn poll(&self, slug: &str, project: &str) -> Value {
        let inner = self.inner.lock();
        let gen = inner.active_gen.get(slug).copied();
        let progress = inner.state.get(slug).map(|row| {
            json!({
                "phase": row.phase,
                "done": row.done,
                "total": row.total,
                "detail": row.detail,
            })
        });
        if let Some(outcome) = inner.outcomes.get(slug) {
            let mut payload = outcome.clone();
            if let Some(obj) = payload.as_object_mut() {
                obj.insert("project".to_string(), json!(project));
                if progress.is_some() {
                    obj.insert("progress".to_string(), progress.unwrap());
                } else if !obj.contains_key("progress") {
                    obj.insert("progress".to_string(), Value::Null);
                }
            }
            return payload;
        }
        if gen.is_some() {
            return json!({
                "project": project,
                "gen": gen,
                "status": "pending",
                "progress": progress,
            });
        }
        json!({
            "project": project,
            "gen": Value::Null,
            "status": "idle",
            "progress": Value::Null,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generation_isolated_and_poll_returns_result() {
        let hub = ScanProgressHub::default();
        let slug = "nevada";
        let gen_a = hub.begin(slug);
        hub.update(slug, gen_a, "peak_links", 2, 10, "a");
        let poll_a = hub.poll(slug, slug);
        assert_eq!(poll_a["status"], "pending");
        assert_eq!(poll_a["progress"]["phase"], "peak_links");

        let gen_b = hub.begin(slug);
        assert!(gen_b > gen_a);
        hub.update(slug, gen_a, "peak_links", 9, 10, "stale");
        assert!(hub.poll(slug, slug)["progress"].is_null());

        hub.update(slug, gen_b, "rf", 0, 0, "active");
        assert_eq!(hub.poll(slug, slug)["progress"]["detail"], "active");

        hub.finish(slug, gen_b, "done", Some(json!({"meta": {"n_candidates": 3}})), None, None);
        let done = hub.poll(slug, slug);
        assert_eq!(done["status"], "done");
        assert_eq!(done["result"]["meta"]["n_candidates"], 3);
    }

    #[test]
    fn idle_when_no_scan() {
        let hub = ScanProgressHub::default();
        let poll = hub.poll("x", "x");
        assert_eq!(poll["status"], "idle");
    }
}
