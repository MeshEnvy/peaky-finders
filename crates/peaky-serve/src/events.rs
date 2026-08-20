//! SSE event hub for project updates.

use std::collections::HashMap;
use std::time::Duration;

use parking_lot::Mutex;
use serde_json::{json, Value};
use tokio::sync::broadcast;

const CAPACITY: usize = 256;

#[derive(Clone, Debug)]
pub struct ServeEvent {
    pub name: String,
    pub data: Value,
}

#[derive(Clone, Default)]
pub struct ServeEventHub {
    inner: std::sync::Arc<Mutex<HashMap<String, broadcast::Sender<ServeEvent>>>>,
}

impl ServeEventHub {
    pub fn publish(&self, project_slug: &str, name: impl Into<String>, data: Value) {
        let tx = self.sender(project_slug);
        let _ = tx.send(ServeEvent {
            name: name.into(),
            data,
        });
    }

    pub fn subscribe(&self, project_slug: &str) -> broadcast::Receiver<ServeEvent> {
        self.sender(project_slug).subscribe()
    }

    fn sender(&self, project_slug: &str) -> broadcast::Sender<ServeEvent> {
        let mut map = self.inner.lock();
        map.entry(project_slug.to_string())
            .or_insert_with(|| broadcast::channel(CAPACITY).0)
            .clone()
    }
}

pub fn format_sse_event(name: &str, data: &Value) -> String {
    format!(
        "event: {name}\ndata: {}\n\n",
        serde_json::to_string(data).unwrap_or_else(|_| "{}".to_string())
    )
}

pub fn sse_keepalive_interval() -> Duration {
    Duration::from_secs(15)
}

pub fn sse_hello(project_slug: &str) -> String {
    format_sse_event("hello", &json!({ "project": project_slug }))
}
