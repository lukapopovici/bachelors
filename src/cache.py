import json

import redis

from src.config import REDIS_URL

redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)


def cache_get(key: str):
    try:
        value = redis_client.get(key)
        return json.loads(value) if value is not None else None
    except (redis.RedisError, ValueError):
        return None


def cache_set(key: str, value, ttl_seconds: int):
    try:
        redis_client.setex(key, ttl_seconds, json.dumps(value))
    except (redis.RedisError, TypeError, ValueError):
        pass


def cache_delete(key: str):
    try:
        redis_client.delete(key)
    except redis.RedisError:
        pass


def cache_invalidate_prefix(prefix: str):
    try:
        keys = redis_client.scan_iter(match=f"{prefix}*")
        for key in keys:
            redis_client.delete(key)
    except redis.RedisError:
        pass