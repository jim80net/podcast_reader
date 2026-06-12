"""Public event-publish seam shared by the job store and the pack manager.

Per S6: pack installer progress rides the same ``GET /v1/events`` SSE stream
as job events, which forces a design decision under mypy strict — rather
than the pack manager reaching into job-store privates, the fan-out is a
public :class:`EventBus`, constructed in ``serve_engine`` and handed to both
producers. Events are self-describing by ``kind`` (no envelope, per Q5);
pack events never carry a ``job_id`` field, because ``job_id`` presence is
the discriminator existing renderer consumers actually use.

Fan-out discipline (moved verbatim from the job store): SSE clients
subscribe via bounded per-client queues; when a queue is full the oldest
event is dropped, and a queue that stays full for
``SUBSCRIBER_FULL_STREAK_LIMIT`` consecutive publishes is pruned as
abandoned (cleanup never depends on GC).
"""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from podcast_reader.types import PipelineEvent

SUBSCRIBER_QUEUE_SIZE = 256
SUBSCRIBER_FULL_STREAK_LIMIT = 100


class EventBus:
    """Bounded fan-out of self-describing events to SSE subscriber queues."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # subscriber queue -> count of consecutive publishes it has been full for
        self._subscribers: dict[queue.Queue[PipelineEvent], int] = {}

    def subscribe(self) -> queue.Queue[PipelineEvent]:
        """Register a bounded event queue for SSE fan-out."""
        q: queue.Queue[PipelineEvent] = queue.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers[q] = 0
        return q

    def unsubscribe(self, q: queue.Queue[PipelineEvent]) -> None:
        """Remove a subscriber queue (safe to call twice)."""
        with self._lock:
            self._subscribers.pop(q, None)

    @property
    def subscriber_count(self) -> int:
        """Number of live SSE subscribers (used by disconnect-cleanup tests)."""
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: PipelineEvent) -> None:
        """Fan *event* out to every subscriber, pruning abandoned consumers."""
        with self._lock:
            subscribers = list(self._subscribers)
        full = {q: self._offer(q, event) for q in subscribers}
        with self._lock:
            for q, was_full in full.items():
                if q not in self._subscribers:
                    continue  # unsubscribed while we were publishing
                self._subscribers[q] = self._subscribers[q] + 1 if was_full else 0
                if self._subscribers[q] >= SUBSCRIBER_FULL_STREAK_LIMIT:
                    del self._subscribers[q]  # abandoned consumer

    @staticmethod
    def _offer(q: queue.Queue[PipelineEvent], event: PipelineEvent) -> bool:
        """Enqueue *event*, dropping the oldest when full; True when it was full."""
        was_full = False
        while True:
            try:
                q.put_nowait(event)
                return was_full
            except queue.Full:
                was_full = True
                try:
                    q.get_nowait()  # drop the oldest event
                except queue.Empty:  # pragma: no cover — racing consumer
                    continue
