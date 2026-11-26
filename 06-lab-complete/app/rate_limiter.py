"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window log)
  • Vượt giới hạn → HTTP 429 + header Retry-After
  • State lưu trong Redis (sorted set: score = thời điểm request) để mọi worker /
    instance dùng chung và vẫn còn sau khi restart agent

Toàn bộ ZREMRANGEBYSCORE → ZCARD → ZADD → PEXPIRE chạy trong MỘT Lua script nên
atomic: 2 worker nhận request cùng lúc không thể cùng "lọt" qua bước đếm.
Thời gian lấy từ Redis (TIME) để mọi instance dùng chung một đồng hồ.
"""
import hashlib
import math
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_MS = 60_000

_SLIDING_WINDOW_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, count, now, tonumber(oldest[2])}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, count + 1, now, 0}
"""
_sliding_window = r.register_script(_SLIDING_WINDOW_LUA)


def _key_for(api_key: str) -> str:
    # Không lưu API key thô trong Redis — chỉ lưu hash
    return "ratelimit:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]


def check_rate_limit(key: str) -> dict:
    """Ghi nhận 1 request cho `key`. Vượt giới hạn → raise HTTPException(429)."""
    limit = settings.rate_limit_per_minute
    allowed, count, now_ms, oldest_ms = _sliding_window(
        keys=[_key_for(key)], args=[WINDOW_MS, limit, uuid.uuid4().hex]
    )
    if not allowed:
        retry_after = max(1, math.ceil((int(oldest_ms) + WINDOW_MS - int(now_ms)) / 1000))
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limit_exceeded", "limit_per_minute": limit,
                    "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(limit),
                     "X-RateLimit-Remaining": "0"},
        )
    return {"limit": limit, "remaining": max(0, limit - int(count))}
