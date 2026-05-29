"""Per-project SSE event hub."""

from __future__ import annotations

import threading

from peaky_finders.web.project_events import project_event_hub, publish_project_op


def test_hub_fanout() -> None:
    hub = project_event_hub("test-proj")
    seen: list[str] = []

    def collect() -> None:
        for msg in hub.subscribe(should_stop=lambda: len(seen) >= 1):
            seen.append(str(msg.get("op")))

    t = threading.Thread(target=collect, daemon=True)
    t.start()
    publish_project_op("test-proj", "maps.layer.phase", layer_id="mesh_depth:d1_unique", phase="built")
    t.join(timeout=2.0)
    assert seen == ["maps.layer.phase"]
