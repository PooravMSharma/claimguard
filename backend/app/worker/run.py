"""Starts a worker that processes jobs from the ClaimGuard queue.

Run with: python -m app.worker.run
"""

import logging

from rq import Worker

from app.core.config import get_settings
from app.core.hardening import check_or_exit
from app.core.logging import setup_logging
from app.services.queue import job_queue, redis_conn


def _recover() -> None:
    from app.db.session import SessionLocal
    from app.services.recovery import recover_stuck

    try:
        with SessionLocal() as db:
            recovered = recover_stuck(db)
            db.commit()
        if recovered:
            logging.getLogger(__name__).warning("Marked %d abandoned claim(s) failed: %s", len(recovered),
                                                ", ".join(recovered))
    except Exception:     # a database problem must not stop the worker from starting; health shows it
        logging.getLogger(__name__).exception("Could not check for abandoned claims")


def main() -> None:
    settings = get_settings()
    setup_logging(debug=settings.is_development)
    check_or_exit(settings, "The worker")
    _recover()
    logging.getLogger(__name__).info("Worker listening on queue %r", settings.queue_name)
    Worker([job_queue], connection=redis_conn).work(with_scheduler=False)


if __name__ == "__main__":
    main()
