import ujson

from .logging import logger

log = logger(__name__)


async def get_redis_value(redis_client, key):
    try:
        result = await redis_client.get(key)
        return ujson.loads(result) if result else None
    except Exception as exc:
        log.error(f"(get_redis_value) ({key}) ({type(exc)}): {exc}")


async def publish_redis_message(redis_client, channel, message):
    try:
        result = await redis_client.publish(channel, ujson.dumps(message))
    except Exception as exc:
        log.error(f"(publish_redis_message) ({channel}) ({type(exc)}): {exc}")


async def set_redis_value(redis_client, key, value):
    try:
        result = await redis_client.set(key, ujson.dumps(value))
    except Exception as exc:
        log.error(f"(set_redis_value) ({key} : {value}) ({type(exc)}): {exc}")
