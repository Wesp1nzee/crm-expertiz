from redis.asyncio import ConnectionPool, Redis
from src.app.core.config.settings import settings

_pool: ConnectionPool = ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True, max_connections=100)


async def get_redis_client() -> Redis:
    return Redis(connection_pool=_pool)
