"""Moonwing worker bootstrap.

Entry point that initialises the database session, object store, and
starts a polling loop that picks up queued runs and processes them
through the staging → running → normalizing → completed pipeline.
"""

from __future__ import annotations

import logging
import signal
import time
from queue import Queue
from uuid import UUID

from sqlalchemy import select

from moonwing.config import Settings
from moonwing.db.models import Run
from moonwing.db.session import build_session_factory
from moonwing.services.run_schedule_service import materialize_due_schedules
from moonwing.worker.object_store import MinioObjectStore
from moonwing.worker.tasks import ObjectStore, enqueue_run, process_next

logger = logging.getLogger("moonwing.worker")

# Graceful shutdown flag
_shutdown = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _shutdown
    logger.info("received signal %s — shutting down after current job", signum)
    _shutdown = True


def bootstrap(settings: Settings | None = None) -> dict:
    """Initialise all worker dependencies and return them as a dict.

    Useful both for the ``main()`` poll loop and for tests that need a
    fully wired worker environment without starting the loop.
    """
    settings = settings or Settings()
    session_factory = build_session_factory(settings.database_url)
    object_store: ObjectStore = MinioObjectStore(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        bucket=settings.object_storage_bucket,
        secure=settings.object_storage_secure,
    )

    logger.info(
        "worker bootstrapped — db=%s bucket=%s clearwing=%s",
        settings.database_url,
        settings.object_storage_bucket,
        settings.clearwing_binary,
    )

    return {
        "settings": settings,
        "session_factory": session_factory,
        "object_store": object_store,
        "clearwing_binary": settings.clearwing_binary,
    }


def poll_once(
    *,
    session_factory,
    object_store: ObjectStore,
    queue: Queue,
) -> int:
    """Scan for QUEUED runs and enqueue them for processing.

    Returns the number of runs enqueued.
    """
    session = session_factory()
    try:
        mat = materialize_due_schedules(session)
        session.commit()
        if mat:
            logger.info("materialized %d scheduled run(s)", mat)

        queued_runs = (
            session.execute(
                select(Run.id)
                .where(Run.status == "queued")
                .order_by(Run.created_at)
                .limit(10)
            )
            .all()
        )

        for (run_id,) in queued_runs:
            enqueue_run(queue=queue, run_id=run_id)

        return len(queued_runs)
    finally:
        session.close()


def drain_queue(
    *,
    session_factory,
    object_store: ObjectStore,
    queue: Queue,
) -> list[UUID]:
    """Process all jobs currently in the queue. Returns processed run IDs."""
    processed: list[UUID] = []
    while not queue.empty():
        session = session_factory()
        try:
            run_id = process_next(
                queue=queue,
                session=session,
                object_store=object_store,
            )
            if run_id is not None:
                processed.append(run_id)
        finally:
            session.close()
    return processed


def main() -> None:
    """Worker entry point — bootstrap and poll indefinitely."""
    settings = Settings()
    if settings.log_json_to_stdout:
        from moonwing.services.logging_json import configure_json_stdout_logging

        configure_json_stdout_logging()
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    env = bootstrap(settings)
    queue: Queue = Queue()
    poll_interval = 2.0  # seconds

    logger.info("worker entering poll loop (interval=%.1fs)", poll_interval)

    while not _shutdown:
        enqueued = poll_once(
            session_factory=env["session_factory"],
            object_store=env["object_store"],
            queue=queue,
        )
        if enqueued:
            logger.info("enqueued %d run(s)", enqueued)

        processed = drain_queue(
            session_factory=env["session_factory"],
            object_store=env["object_store"],
            queue=queue,
        )
        if processed:
            logger.info("processed %d run(s): %s", len(processed), processed)

        time.sleep(poll_interval)

    logger.info("worker shut down cleanly")


if __name__ == "__main__":
    main()
