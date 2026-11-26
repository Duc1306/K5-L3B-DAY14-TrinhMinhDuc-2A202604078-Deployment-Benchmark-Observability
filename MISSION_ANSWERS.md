# Day 14 Lab - Mission Answers

> Môi trường chạy: Ubuntu trên WSL2, Python 3.12.3 (`os.name == "posix"`), Docker 28.0.4, Docker Compose v2.34.0.

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

1. **Hardcode secret trong code** (dòng 17–18): `OPENAI_API_KEY = "sk-..."` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ key và mật khẩu DB. Xoá file sau đó cũng không cứu được, vì secret vẫn nằm trong lịch sử git. Muốn đổi key thì phải sửa code và deploy lại.
2. **Ghi secret ra log** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Khi chạy thật, log in ra `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`. Ai đọc được log (dashboard platform, log aggregator) cũng lấy được key.
3. **Dùng `print()` thay cho logging** (dòng 33, 34, 38): không có level, timestamp hay cấu trúc, nên log aggregator không lọc hoặc search theo field được. Ngoài ra code in nguyên văn câu hỏi và câu trả lời của người dùng, dễ lộ dữ liệu cá nhân.
4. **Config cố định trong code** (dòng 21–22): `DEBUG = True`, `MAX_TOKENS = 500`. Không đổi được giữa dev, staging và prod nếu không sửa code. Debug luôn bật cả trên production.
5. **Không có health check** (dòng 42): không có `/health` và `/ready`. Khi gọi thử thì `GET /health` → **404 Not Found**. Platform không biết agent còn sống hay đã sẵn sàng nhận request, nên không tự restart và load balancer không biết khi nào ngừng gửi traffic.
6. **Bind `host="localhost"`** (dòng 51): chỉ nhận kết nối từ chính máy đó. Mình đã thử gọi từ IP của interface `eth0` (`172.25.56.94:8000`): **connection refused**. Chạy trong container hay trên cloud thì không ai gọi vào được.
7. **Port cố định 8000** (dòng 52): Railway và Render cấp port qua biến `PORT`. App không đọc biến này nên health check của platform fail và deploy bị đánh dấu lỗi.
8. **`reload=True` khi chạy thật** (dòng 53): log có dòng `Started reloader process ... using WatchFiles`. Reloader tốn CPU để theo dõi file, chạy thêm một process cha, và có thể restart app giữa chừng khi file thay đổi.
9. **Không xử lý graceful shutdown**: không có lifespan/cleanup hay handler SIGTERM. App không có chỗ để đóng kết nối (DB, Redis) hoặc ghi log khi bị tắt.
10. **Nhận `question` qua query string** (dòng 31, `def ask_agent(question: str)`): gửi JSON body `{"question":"Hello"}` bị **422** (`"loc":["query","question"]`). Câu hỏi của người dùng nằm trên URL, nên bị ghi vào access log của proxy và uvicorn và bị giới hạn độ dài URL. Input cũng không được validate.

### Exercise 1.2: Chạy basic version

Chạy `python app.py` trong `develop/` (WSL):

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 422 Unprocessable Entity

{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

{"detail":"Not Found"}
```

Log của server (secret bị in ra):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [1869] using WatchFiles
Starting agent on localhost:8000...
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.
INFO:     127.0.0.1:51200 - "POST /ask?question=Hello HTTP/1.1" 200 OK
```

**Quan sát:** app trả lời được, nhưng chưa sẵn sàng cho production. Nó chỉ nghe trên localhost, lộ key trong log, không có health check, và lệch hợp đồng API (chỉ nhận query, không nhận JSON).

### Exercise 1.3: Comparison table

Chạy `python app.py` trong `production/`:

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T07:29:20.661465+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
```

Thêm vài kiểm tra:
- Gọi `/health` từ IP `eth0` → **200** (bind `0.0.0.0`).
- `PORT=9000 python app.py` → `/health` trên cổng 9000 trả **200**.
- Gửi `kill -TERM` → log có `Shutting down … Waiting for application shutdown … Application shutdown complete`, process tự thoát.

| Feature | Develop | Production | Tại sao quan trọng? |
|---------|---------|------------|---------------------|
| Config | Hardcode trong code (`DEBUG = True`, `MAX_TOKENS = 500`) | Đọc env var qua `config.py` (`os.getenv(...)` cho mọi field, dòng 17–35) | Cùng một code (cùng image) chạy được ở dev, staging và prod, chỉ khác env. Đổi cấu hình không cần sửa code hay build lại. |
| Secrets | Key và mật khẩu DB nằm trong source, còn bị in ra log | Đọc từ env (`OPENAI_API_KEY`, `AGENT_API_KEY`), không log. `validate()` dừng app ngay nếu thiếu `AGENT_API_KEY` khi `ENVIRONMENT=production` | Secret trong git là lộ vĩnh viễn (nằm trong lịch sử commit). Secret đặt trên platform thì xoay key được mà không cần deploy lại. Dừng sớm khi thiếu key tốt hơn chạy mà không có bảo vệ. |
| Health check | Không có (`/health` → 404) | `/health` (liveness: uptime, version) và `/ready` (readiness: trả 503 khi `is_ready=False`) | Liveness cho platform biết khi nào cần restart container chết. Readiness cho load balancer biết khi nào được gửi traffic vào, nên không có request rơi vào instance đang khởi động hoặc đang tắt. |
| Logging | `print()`, không level, in cả secret và nội dung câu hỏi | `logging` với format JSON, chỉ ghi metadata (`question_length`, `client_ip`), không ghi secret | Log có cấu trúc thì log aggregator (Loki, Datadog) lọc và đếm theo field được. Không ghi secret hay nội dung người dùng để tránh lộ dữ liệu. |
| Shutdown | Đột ngột, không cleanup | `lifespan` (startup/shutdown) + handler SIGTERM. Uvicorn chờ request đang chạy xong rồi mới thoát | Platform gửi SIGTERM khi deploy hoặc scale down. Graceful shutdown giúp request đang chạy không bị cắt giữa chừng, và kết nối được đóng gọn. |
| Host binding | `localhost`: không truy cập được từ ngoài (đã thử: connection refused) | `0.0.0.0` (env `HOST`) | Trong container hay trên cloud, traffic đi vào qua interface mạng chứ không qua loopback. Bind localhost thì app chạy nhưng không ai gọi được. |
| Port | Cố định `8000` | `PORT` env (đã thử `PORT=9000`) | Railway và Render cấp port động qua `PORT`. Không đọc biến này thì health check của platform fail. |
| Reload / debug | `reload=True` luôn bật | `reload=settings.debug`, mặc định `DEBUG=false` | Reloader tốn tài nguyên, tạo thêm process và có thể restart app bất ngờ. Chỉ dùng khi dev. |
| Input `/ask` | Query string, JSON → 422 | JSON body, thiếu `question` → 422 có thông báo rõ | Body JSON hợp chuẩn REST, không làm lộ nội dung câu hỏi trong URL và access log, mở rộng thêm field được (vd `user_id`). |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ cho các frontend được phép gọi API từ trình duyệt. |

**Những điểm mình thấy bản production vẫn chưa hoàn hảo** (rút ra khi chạy thật, cần nhớ khi làm Part 6):

- **Log JSON không hiện ra.** Khi chạy, chỉ thấy `WARNING:root:OPENAI_API_KEY not set — using mock LLM` theo format mặc định, không có dòng `agent_request` nào. Nguyên nhân: `config.py` gọi `logging.warning()` lúc import (dòng 45), trước `logging.basicConfig(...)` trong `app.py` (dòng 28). Lệnh `logging.warning()` tự cấu hình root logger ở mức WARNING, nên `basicConfig` sau đó không còn tác dụng và mọi `logger.info` bị bỏ qua. Kiểm tra: sau `import app` thì root level = `WARNING`, format = `%(levelname)s:%(name)s:%(message)s`.
- **Kể cả khi format đó chạy được, dòng log cũng không phải JSON hợp lệ.** Ví dụ `{"time":"...","level":"INFO","msg":"{"event": "agent_request", ...}"}`: phần `msg` chứa JSON nhưng không escape dấu ngoặc kép, nên `json.loads` báo lỗi `Expecting ',' delimiter`. Part 6 yêu cầu mỗi dòng log là một JSON object hợp lệ, nên phải dựng cả dict rồi `json.dumps` một lần (hoặc viết `logging.Formatter` riêng).
- **`.env` không được nạp.** `config.py` chỉ dùng `os.getenv`, không gọi `load_dotenv()`, nên bước `cp .env.example .env` không thay đổi gì. Muốn đổi config thì phải export biến môi trường. Đây cũng là cách platform cloud làm.
- Handler SIGTERM tự viết chỉ ghi log. Phần "graceful" thực chất do uvicorn làm: dừng nhận kết nối mới, chờ request đang chạy xong, rồi chạy phần shutdown của `lifespan`.

### Checkpoint 1

- **Vì sao hardcode secret nguy hiểm?** Code thường được push lên git, copy sang máy khác và build vào image. Secret đi theo tới mọi nơi và nằm vĩnh viễn trong lịch sử commit. Muốn xoay key thì phải sửa code và deploy lại.
- **Dùng env var thế nào?** Đọc bằng `os.getenv("NAME", default)` ở một chỗ tập trung (`config.py`), ép kiểu (`int(...)`), dừng app ngay nếu thiếu biến bắt buộc. Liệt kê các biến trong `.env.example` (không có giá trị thật). Giá trị thật đặt trên platform hoặc export khi chạy.
- **Health check để làm gì?** `/health` (liveness) trả lời câu hỏi "process còn sống không?": fail thì platform restart. `/ready` (readiness) trả lời câu hỏi "nhận traffic được chưa?": fail thì load balancer tạm ngừng gửi request nhưng không restart.
- **Graceful shutdown là gì?** Khi nhận SIGTERM: ngừng nhận request mới, xử lý xong request đang chạy, đóng kết nối, rồi thoát với exit code 0. Nhờ vậy deploy hoặc scale down không làm người dùng nhận lỗi.

## Part 2: Docker

_(CP2: chưa làm)_

## Part 3: Cloud Deployment

_(CP6: chưa làm)_

## Part 4: API Security

_(CP3: chưa làm)_

## Part 5: Scaling & Reliability

_(CP4: chưa làm)_

## Part 6: Final Project — output `check_production_ready.py`

_(CP5: chưa làm. Dán phần "📋 Checklist Deliverable" ở đây.)_

## AI log

| # | Hỏi gì | Nhận gì | Tự sửa / tự kiểm tra thế nào |
|---|--------|---------|-------------------------------|
| 1 | Nhờ Claude Code đọc README, RULES, CHECKPOINTS, SUBMISSION, RUBRIC và tóm tắt yêu cầu lab | Bản tóm tắt thang điểm, luật, lịch CP0–CP8 và các TODO của Part 6 | Tự đối chiếu lại với RUBRIC.md (tổng 120, Part 6 = 60) |
| 2 | CP0: kiểm tra môi trường | Phát hiện Docker Desktop chưa bật và WSL thiếu `pip`/`venv`; cài `python3.12-venv`, `python3-pip`; tạo venv `~/.venvs/day14` | `python -c "import os; print(os.name)"` in `posix`; `docker run --rm hello-world` chạy được trong WSL; `check_production_ready.py --static-only` chạy được (0/12 như kỳ vọng với bản khung) |
| 3 | CP1: tìm anti-pattern, chạy bản develop/production, điền bảng so sánh | Danh sách anti-pattern kèm số dòng, script curl chạy hai bản, phát hiện lỗi logging của bản production | Tự chạy lại curl và xem log; tự kiểm lỗi logging bằng cách in root logger level sau `import app` và thử `json.loads` dòng log |
