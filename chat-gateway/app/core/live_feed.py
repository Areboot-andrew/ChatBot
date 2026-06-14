"""
In-memory live event bus for the admin 'Жива стрічка' (Діалоги page).

Every channel runs the same pipeline with a `trace` callback. Here that callback
also publishes each step to per-tenant subscribers the moment it fires — so the
admin sees what the routes and the model received in real time, step by step,
instead of one batch after the turn is logged to Postgres.

Pure in-process pub/sub (no Redis): the admin SSE endpoint subscribes, the
pipeline publishes. Events are also kept in a small ring buffer so a page that
connects mid-turn can backfill the last few events.
"""
import asyncio
import itertools
import time
from collections import deque, defaultdict

# tenant_id(str) -> set[asyncio.Queue]
_subscribers: dict[str, set] = defaultdict(set)
# tenant_id(str) -> deque[event]  (recent events for late subscribers)
_recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
_seq = itertools.count(1)


def publish(tenant_id, event: dict) -> None:
    """Fan out one event to every live subscriber of this tenant. Best-effort:
    a slow/full subscriber never blocks the pipeline."""
    if not tenant_id:
        return
    key = str(tenant_id)
    event = {"seq": next(_seq), "ts": time.time(), **event}
    _recent[key].append(event)
    for q in list(_subscribers.get(key, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


def subscribe(tenant_id) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    _subscribers[str(tenant_id)].add(q)
    return q


def unsubscribe(tenant_id, q: asyncio.Queue) -> None:
    subs = _subscribers.get(str(tenant_id))
    if subs:
        subs.discard(q)
        if not subs:
            _subscribers.pop(str(tenant_id), None)


def recent(tenant_id, after_seq: int = 0) -> list:
    return [e for e in _recent.get(str(tenant_id), ()) if e["seq"] > after_seq]
