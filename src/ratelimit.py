import hashlib
import uuid

from fastapi import HTTPException, Request
from redis.exceptions import RedisError

from src.cache import redis_client

_SLIDING_WINDOW_SCRIPT = """
local now_parts = redis.call('TIME')
local now = now_parts[1] * 1000 + math.floor(now_parts[2] / 1000)
local window_ms = tonumber(ARGV[1]) * 1000
local limit = tonumber(ARGV[2])
local cutoff = now - window_ms
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')[2]
    local retry_after = math.max(1, math.ceil((tonumber(oldest) + window_ms - now) / 1000))
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    return {0, 0, retry_after}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return {1, limit - count - 1, ARGV[1]}
"""


def rate_limit(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    try:
        allowed, remaining, _retry_after = redis_client.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            key,
            window_seconds,
            max_requests,
            uuid.uuid4().hex,
        )
    except RedisError:
        return True, max_requests

    if int(allowed) == 0:
        return False, 0
    return True, int(remaining)


def _client_identifier(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if authorization:
        token = authorization.split(" ", 1)[-1]
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
    return request.client.host if request.client else "unknown"


def make_rate_limiter(max_requests: int, window_seconds: int):
    async def rate_limit_dependency(request: Request):
        client_id = _client_identifier(request)
        key = f"ratelimit:{request.url.path}:{client_id}"
        allowed, remaining = rate_limit(key, max_requests, window_seconds)
        request.state.rate_limit = {
            "limit": max_requests,
            "remaining": remaining,
        }
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(window_seconds)},
            )
        return remaining

    return rate_limit_dependency