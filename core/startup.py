"""In-process pending URL handoff for cold-start protocol downloads.

When N13 is launched by a ``dldm://`` request while it is not running, the URL
is parked here until the GUI finishes initialising (TaskManager, Live Server,
API, rules).  The GUI drains the queue once everything is ready, so a startup
URL is never dropped and never sent to a server that is not ready yet.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("n13")

_lock = threading.Lock()
_pending: list[str] = []


def store(url: str) -> None:
    """Queue a startup URL for the application to consume once ready."""
    url = (url or "").strip()
    if not url:
        return
    with _lock:
        if url not in _pending:
            _pending.append(url)
            log.info("[4] Startup URL stored (pending): %s", url)


def drain() -> list[str]:
    """Return and clear every pending startup URL."""
    with _lock:
        urls = list(_pending)
        _pending.clear()
    if urls:
        log.info("[9] Pending startup URL(s) drained: %d", len(urls))
    return urls
