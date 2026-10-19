from redis import Redis
from rq import Queue

from app.core.config import get_settings

settings = get_settings()

redis_conn = Redis.from_url(settings.redis_url)
job_queue = Queue(settings.queue_name, connection=redis_conn)