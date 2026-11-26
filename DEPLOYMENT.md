# Deployment Information

## Public URL

https://day14-agent-opr7.onrender.com

## Platform

**Render** (Blueprint), deploy agent Part 6 từ thư mục `06-lab-complete/` của repo này.

| Thành phần | Render resource | Ghi chú |
|------------|-----------------|---------|
| Agent | Web Service `day14-agent` (runtime `docker`, plan free) | Build từ `06-lab-complete/Dockerfile`, health check `/health` |
| Redis | Key Value `day14-redis` (plan free) | `ipAllowList: []` → chỉ service nội bộ Render kết nối được |

Cấu hình: [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml). Khi tạo Blueprint, ô **Blueprint Path** = `06-lab-complete/render.yaml`; `rootDir: 06-lab-complete` nên `dockerfilePath: ./Dockerfile` và `dockerContext: .` tính từ thư mục đó.

## Test Commands

Đặt biến trước (key lấy ở tab **Environment** của service `day14-agent` trên Render, không ghi vào file):

```bash
export BASE=https://day14-agent-opr7.onrender.com
read -s AGENT_API_KEY && export AGENT_API_KEY
```

### Health check (liveness)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl "$BASE/health"
# {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":138.3}
```

### Readiness (Redis)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl "$BASE/ready"
# {"ready":true,"redis":"ok"}
```

### API không có key → 401

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'                         # 401
# {"detail":"Missing or invalid API key. Send header: X-API-Key: <key>"}
```

### API có key → 200

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'                                                             # 200
```

### Rate limit → 429 (RATE_LIMIT_PER_MINUTE=10)

```bash
for i in $(seq 1 12); do
  curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" \
    -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"test"}'
done; echo
# 200 ... 200 429 429
```

## Kết quả kiểm tra

| Lệnh | Mong đợi | Kết quả |
|------|----------|---------|
| `GET /health` | 200 | **200** `{"status":"ok",...,"environment":"production"}` |
| `GET /ready` | 200 | **200** `{"ready":true,"redis":"ok"}` (Redis trên Render kết nối được) |
| `POST /ask` không có key | 401 | **401** |
| `POST /ask` sai key | 401 | **401** |
| `POST /ask` có key | 200 | **200** (xem [`screenshots/test.png`](screenshots/test.png)) |
| `GET /docs` | ẩn khi production | **404** |
| `python check_production_ready.py --public-url https://day14-agent-opr7.onrender.com` | ✅ | `✅ Public URL: GET https://day14-agent-opr7.onrender.com/health → 200` |

## Environment Variables Set

Đặt trên Render (khai báo trong `render.yaml`, **không** có giá trị secret nào trong code hay trong git):

| Biến | Nguồn | Giá trị |
|------|-------|---------|
| `AGENT_API_KEY` | `generateValue: true` | Render tự sinh (secret, xem trên dashboard) |
| `REDIS_URL` | `fromService` → `day14-redis.connectionString` | URL nội bộ của Key Value |
| `ENVIRONMENT` | `render.yaml` | `production` (bắt buộc có `AGENT_API_KEY`, tắt `/docs`) |
| `RATE_LIMIT_PER_MINUTE` | `render.yaml` | `10` |
| `DAILY_BUDGET_USD` | `render.yaml` | `1.0` |
| `MONTHLY_BUDGET_USD` | `render.yaml` | `10.0` |
| `PORT` | Render tự inject | Dockerfile `CMD` dùng `${PORT:-8000}` |

## Lưu ý gói free

- Web service **ngủ sau 15 phút** không có traffic; request đầu tiên đánh thức mất ~1 phút. Gọi `/health` trước khi chấm/chụp ảnh.
- Key Value free **không lưu xuống đĩa**: Redis restart thì mất state rate limit / budget (chấp nhận được cho lab).

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
