"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • App vẫn KHỞI ĐỘNG ĐƯỢC khi Redis chưa sẵn sàng: redis.from_url() chỉ tạo
    connection pool, chưa kết nối — kết nối thật chỉ mở ở lệnh đầu tiên.
  • redis_healthy() dùng cho GET /ready: Redis không kết nối được → False
"""
import redis
from redis.exceptions import RedisError

from app.config import settings

r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,   # Redis chết → fail nhanh, không treo request / probe
    socket_timeout=1,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except (RedisError, OSError):
        return False


def close_redis() -> None:
    try:
        r.close()
    except (RedisError, OSError):
        pass
