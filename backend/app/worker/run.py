"""Starts a worker that processes jobs from the ClaimGuard queue.

Run with: python -m app.worker.run
"""

import logging

from rq import Worker

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.queue import job_queue, redis_conn


def main() -> None:
    settings = get_settings()
    setup_logging(debug=settings.is_development)
    logging.getLogger(__name__).info("Worker listening on queue %r", settings.queue_name)
    Worker([job_queue], connection=redis_conn).work(with_scheduler=False)


if __name__ == "__main__":
    main()