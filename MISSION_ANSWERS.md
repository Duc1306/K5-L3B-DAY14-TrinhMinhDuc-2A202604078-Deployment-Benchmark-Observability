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

> Build từ **gốc repo** (README lưu ý số 4), vì Dockerfile `COPY` theo đường dẫn tính từ gốc. Build context là gốc repo nên Docker dùng `.dockerignore` ở gốc, không dùng `02-docker/develop/.dockerignore`.
> Lúc đầu Docker Hub trả `429 Too Many Requests` (giới hạn pull khi chưa đăng nhập), nên mình pull các base image `python:3.11`, `python:3.11-slim`, `redis:7-alpine`, `nginx:alpine`, `qdrant/qdrant:v1.9.0` qua `mirror.gcr.io` (mirror công khai của Docker Hub) rồi `docker tag` lại đúng tên. Dockerfile giữ nguyên.

### Exercise 2.1: Dockerfile questions

File `02-docker/develop/Dockerfile`:

1. **Base image:** `python:3.11` (dòng 8), bản Python đầy đủ chạy trên Debian, có sẵn gcc, git và các header để build. Máy mình đo được `python:3.11` = **1.6GB** trong `docker images`.
2. **Working directory:** `/app` (dòng 11, `WORKDIR /app`). Các lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối từ `/app`. Thư mục được tạo tự động nếu chưa có.
3. **Vì sao `COPY requirements.txt` đứng trước `COPY app.py`?** Mỗi lệnh trong Dockerfile tạo một layer. Docker chỉ dùng lại cache của một layer khi layer đó **và mọi layer trước nó** không đổi. `requirements.txt` hiếm khi đổi, còn code thì đổi liên tục. Đặt `COPY requirements.txt` + `RUN pip install` lên trước thì sửa code chỉ làm mất cache từ bước `COPY app.py` trở đi, còn bước `pip install` (bước lâu nhất) vẫn được cache. Đảo thứ tự (`COPY . .` trước) thì mỗi lần sửa code đều phải cài lại toàn bộ dependency.
   *Đo thật:* build lần đầu mất **70s**. Build lại khi không đổi gì mất **2s**, cả 6 bước `WORKDIR`, `COPY requirements.txt`, `RUN pip install`, `COPY app.py`, `RUN mkdir`, `COPY utils/...` đều `CACHED`.
4. **CMD và ENTRYPOINT khác nhau thế nào?** `CMD ["python", "app.py"]` là lệnh **mặc định**, ghi đè được hoàn toàn khi chạy (`docker run my-agent:develop python -c "..."` sẽ chạy lệnh mới thay cho `app.py`). `ENTRYPOINT` là phần **cố định** của lệnh: tham số của `docker run` (hoặc `CMD`) được nối vào sau nó, muốn đổi phải dùng `--entrypoint`. Cách dùng thường gặp: `ENTRYPOINT ["uvicorn"]` + `CMD ["main:app", "--port", "8000"]`, khi chạy chỉ cần đổi tham số. Cả hai nên viết dạng exec (mảng JSON) để process là PID 1 và nhận được SIGTERM trực tiếp. Dạng shell (`CMD python app.py`) chạy qua `/bin/sh -c`, nên signal có thể không tới được app.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
develop build: 70s

$ docker run -d --name cp2-dev -p 8000:8000 my-agent:develop
$ docker ps --filter name=cp2-dev
CONTAINER ID   IMAGE              STATUS         PORTS
2124a5d2c556   my-agent:develop   Up 3 seconds   0.0.0.0:8000->8000/tcp

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
server: uvicorn
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"container":true}
```

Debug bên trong container bằng `docker exec` và `docker logs`:

```text
$ docker exec cp2-dev sh -c "whoami; python --version; ls /app /app/utils"
root
Python 3.11.18
/app:  __pycache__  app.py  requirements.txt  utils
/app/utils:  __pycache__  mock_llm.py

$ docker logs cp2-dev
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:52758 - "POST /ask?question=What%20is%20Docker%3F HTTP/1.1" 200 OK
```

**Quan sát:** container bản develop chạy bằng **root**. Nếu app bị khai thác, kẻ tấn công có quyền root trong container.

### Exercise 2.3: Multi-stage build — Image size comparison

File `02-docker/production/Dockerfile`:

- **Stage 1 (`builder`, dòng 16–29):** từ `python:3.11-slim`, cài thêm `gcc` và `libpq-dev` (build tool để compile các package có phần C), rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ dùng để build, không được deploy.
- **Stage 2 (`runtime`, dòng 36–72):** bắt đầu lại từ một `python:3.11-slim` **sạch**, chỉ `COPY --from=builder /root/.local` (các package đã cài) cùng `main.py` và `utils/mock_llm.py`. Stage này tạo user `appuser` và chạy bằng `USER appuser` (non-root), đặt `PATH`/`PYTHONPATH`, thêm `HEALTHCHECK` gọi `/health`, và `CMD` chạy uvicorn với `--workers 2`.
- **Vì sao image nhỏ hơn?**
  1. Base `slim` thay cho bản đầy đủ: `python:3.11-slim` = **187MB**, còn `python:3.11` = **1.6GB**. Đây là phần chênh lớn nhất.
  2. Multi-stage: `gcc`, `libpq-dev`, cache của apt và pip chỉ nằm ở stage `builder`, không vào image cuối. Image cuối chỉ có thêm đúng một layer package **40.8MB** (`COPY /root/.local ...` trong `docker history`), còn các layer khác chỉ vài chục KB.
  3. Image nhỏ thì pull và deploy nhanh hơn, tốn ít dung lượng registry hơn, và ít phần mềm thừa nên ít lỗ hổng bảo mật (CVE) hơn.

Số đo bằng `docker images` (build lần đầu của advanced mất 60s):

```text
$ docker images my-agent
REPOSITORY   TAG        IMAGE ID       CREATED              SIZE
my-agent     advanced   797f7db468bc   5 seconds ago        240MB
my-agent     develop    61333fb9c69c   About a minute ago   1.67GB
```

- Develop: **1670 MB** (1.67GB)
- Production (advanced): **240 MB**
- Difference: **giảm 85.6%** (`(1670 − 240) / 1670`). Image production nhỏ bằng khoảng 1/7 image develop.
- Đối chiếu bằng `docker image inspect --format '{{.Size}}'` (kích thước nén): develop 426,320,090 B và advanced 57,409,215 B, tức **giảm 86.5%**, cùng kết luận.

Container bản advanced: `whoami` → `appuser`; process chạy `/usr/local/bin/python3.11 /home/appuser/.local/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2`; `docker inspect` → `health: healthy` (HEALTHCHECK hoạt động).

### Exercise 2.4: Docker Compose stack

Chạy: `touch .env.local && docker compose -p k5-day14-docker up -d --build` trong `02-docker/production/` (stack lên sau 19s).

**Architecture diagram:**

```text
                       Host (máy mình)
                            │  :80 (và :443, chưa có cert)
                            ▼
┌──────────────────── network "internal" (bridge) ─────────────────────┐
│                                                                       │
│   ┌─────────────────────────────┐                                    │
│   │ nginx (nginx:alpine)        │  reverse proxy + load balancer     │
│   │ - rate limit 10 r/s/IP,     │  upstream agent_backend            │
│   │   burst 20 → 429            │  = agent:8000                      │
│   │ - security headers          │                                    │
│   └──────────────┬──────────────┘                                    │
│                  │ http://agent:8000  (DNS nội bộ của Docker)         │
│                  ▼                                                    │
│   ┌─────────────────────────────┐                                    │
│   │ agent (build từ Dockerfile, │  uvicorn --workers 2, user appuser │
│   │ target: runtime)            │  KHÔNG publish port ra host        │
│   └──────┬───────────────┬──────┘                                    │
│          │ REDIS_URL     │ QDRANT_URL                                │
│          ▼               ▼                                           │
│   ┌────────────┐   ┌──────────────┐                                  │
│   │ redis:7    │   │ qdrant v1.9  │                                  │
│   │ :6379      │   │ :6333/6334   │                                  │
│   │ vol        │   │ vol          │                                  │
│   │ redis_data │   │ qdrant_data  │                                  │
│   └────────────┘   └──────────────┘                                  │
└───────────────────────────────────────────────────────────────────────┘
Thứ tự khởi động: redis + qdrant (healthy) → agent → nginx
```

**Services được start:** 4 service.

```text
SERVICE   IMAGE                   STATUS                            PORTS
agent     k5-day14-docker-agent   Up 6 seconds (health: starting)   8000/tcp
nginx     nginx:alpine            Up 5 seconds                      0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 17 seconds (healthy)           6333-6334/tcp
redis     redis:7-alpine          Up 17 seconds (healthy)           6379/tcp
```

**Các service giao tiếp thế nào?**
- Cả 4 service cùng nằm trong network bridge `internal` (`k5-day14-docker_internal`). Docker có DNS nội bộ nên các service gọi nhau bằng **tên service**. Trong container agent: `redis -> 172.20.0.2`, `qdrant -> 172.20.0.3`, `agent -> 172.20.0.4`, `nginx -> 172.20.0.5`; `redis-cli ping` → `PONG`.
- Chỉ **nginx** publish port ra host (80/443). Agent chỉ `8000/tcp` nội bộ: gọi `http://localhost:8000/health` từ host bị **connection refused**, mọi traffic phải đi qua nginx.
- Agent tìm Redis và Qdrant qua env (`REDIS_URL=redis://redis:6379/0`, `QDRANT_URL=http://qdrant:6333`). App mẫu `main.py` chưa thực sự dùng hai service này, chúng có mặt để minh hoạ một stack đầy đủ.
- `depends_on` với `condition: service_healthy` bắt agent chờ redis và qdrant **healthy** mới khởi động (log: `qdrant-1 Healthy`, `redis-1 Healthy` → `agent-1 Starting`).

**Test qua Nginx:**

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":0.4,"version":"2.0.0","timestamp":"2026-11-26T08:00:28.344933"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

Header `Server: nginx` và các security header chứng tỏ response đi qua nginx. Thử thêm rate limit của nginx: gửi liên tiếp 40 request `GET /` thì được **25 × 200, 15 × 429**. Nginx chặn từ trước khi request tới agent, log ghi `limiting requests, excess: 20.510 by zone "api_limit"`.

Dọn stack: `docker compose -p k5-day14-docker down -v`.

### Checkpoint 2

- **Multi-stage giảm dung lượng ở đâu?** Ở base image (slim thay cho full) và ở việc bỏ lại build tool (gcc, header, cache) trong stage builder. Image cuối chỉ chứa runtime cộng package đã cài.
- **Debug container:** `docker logs <container>` xem stdout/stderr; `docker exec -it <container> sh` vào trong container kiểm tra file, user, process; `docker inspect` xem trạng thái health; `docker compose -p <project> logs <service>` cho từng service trong stack.

## Part 3: Cloud Deployment

_(CP6: chưa làm)_

## Part 4: API Security

> Bản develop chạy bằng `AGENT_API_KEY=secret-key-123 python app.py` (README lưu ý số 6). Bản production dùng venv riêng với `04-api-gateway/production/requirements.txt` (cần `pydantic>=2.11`, khác Part 6 ghim `2.9.0`), lấy token ở `POST /auth/token` với `student` / `demo123`. Token trong output dưới đây đã được che (`eyJ***MASKED***`).

### Exercise 4.1: API Key authentication

**API key được check ở đâu?** Trong `04-api-gateway/develop/app.py`:
- Dòng 35: `API_KEY = os.getenv("AGENT_API_KEY", ...)`, key hợp lệ đọc từ biến môi trường.
- Dòng 36: `APIKeyHeader(name="X-API-Key", auto_error=False)` đọc header `X-API-Key`. `auto_error=False` để tự quyết định mã lỗi.
- Dòng 39–54: dependency `verify_api_key()` thực hiện việc so sánh.
- Dòng 70: `_key: str = Depends(verify_api_key)` gắn dependency vào `/ask`. FastAPI chạy nó **trước** thân hàm, nên request sai key không bao giờ chạm tới LLM. `/` và `/health` không gắn dependency nên vẫn public, đúng ý vì platform cần gọi `/health` mà không có key.

**Sai key thì sao?** Thiếu header → **401** `Missing API key`. Có header nhưng sai → **403** `Invalid API key.`. *Lưu ý cho Part 6:* hợp đồng chấm yêu cầu **thiếu hoặc sai đều trả 401**, nên không được chép nguyên logic 403 này.

**Rotate key thế nào?** Đặt giá trị mới cho `AGENT_API_KEY` trên platform hoặc trong env rồi restart app, không cần sửa code. Hạn chế: app chỉ nhận **một** key, nên lúc đổi thì mọi client cũ bị 401 cùng lúc. Cách tốt hơn là cho phép danh sách key (vd `AGENT_API_KEYS=new,old`) trong một khoảng chuyển tiếp: phát key mới cho client, đợi client chuyển xong, rồi bỏ key cũ.

**Điểm chưa an toàn mình thấy trong bản develop:**
- Dòng 35 có giá trị mặc định `"demo-key-change-in-production"`. Quên đặt env thì app vẫn chạy với một key ai cũng biết (nằm công khai trên GitHub). Nên dừng app ngay khi thiếu key.
- Dòng 91–92 in key ra stdout lúc khởi động. Log thật: `API Key: secret-key-123`.
- Dòng 49 so sánh bằng `!=`, nên dùng `secrets.compare_digest` để tránh timing attack.
- Dòng 93 bật `reload=True`.

Test:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2: JWT authentication

**JWT flow** (`production/auth.py`, `production/app.py`):
1. `POST /auth/token` với username/password → `authenticate_user()` so với `DEMO_USERS` (sai → **401** `Invalid credentials`).
2. `create_token()` ký payload `{"sub", "role", "iat", "exp" = now + 60 phút}` bằng HS256 với `JWT_SECRET`.
3. Client gửi `Authorization: Bearer <token>` ở mọi request sau đó.
4. `verify_token()` (dependency của `/ask`, `/me/usage`, `/admin/stats`) gọi `jwt.decode(...)`: kiểm chữ ký và hạn dùng, rồi lấy `username` và `role` **từ chính token**, không tra DB. Đó là ý nghĩa "stateless". Thiếu token → 401, token hết hạn → 401, token giả hoặc sửa → 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"wrong"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***..."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}

$ curl -s ... -d '{"question":""}'          # validation bằng Pydantic (min_length=1) → 422, body:
{"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character",...}]}
```

**Quan sát:**
- **Payload của JWT ai cũng đọc được**, vì nó chỉ là base64, không mã hoá. Decode token của mình không cần secret: `{"sub": "student", "role": "user", "iat": 1795680448, "exp": 1795684048}`. Secret chỉ chống **sửa** payload (sửa `role` thành `admin` thì chữ ký sai → 403), không chống **đọc**. Vì vậy không được đặt dữ liệu nhạy cảm vào payload.
- Restart app xong, token cũ vẫn dùng được (200), vì server không lưu session. Mặt trái: không thu hồi được token trước khi hết hạn, trừ khi đổi `JWT_SECRET` (mọi token cùng mất hiệu lực) hoặc thêm danh sách token bị chặn trong Redis.
- `auth.py` dòng 22: `JWT_SECRET` có giá trị mặc định cố định trong code. Quên đặt env thì ai đọc repo cũng tự ký được token `admin`.
- `/admin/stats` với token student → **403**, với token teacher → 200. Đây là phân quyền theo `role` lấy từ token.

### Exercise 4.3: Rate limiting

**Algorithm:** **Sliding window log** (`production/rate_limiter.py`). Mỗi user có một `deque` lưu timestamp các request (dòng 27). Mỗi lần check: bỏ các timestamp cũ hơn `now − 60s` (dòng 39–40), đếm số còn lại, đủ `max_requests` thì trả **429** kèm `Retry-After` tính từ request cũ nhất trong cửa sổ (dòng 45–62), chưa đủ thì ghi thêm timestamp (dòng 65). Docstring ghi "Sliding Window Counter" nhưng code thật lưu **từng timestamp**, tức là sliding window *log*: chính xác (không bị dồn request ở ranh giới phút như fixed window) nhưng tốn bộ nhớ O(limit) cho mỗi user. Không phải token bucket.

**Limit:** user thường (`student`) **10 request / 60 giây**, admin (`teacher`) **100 request / 60 giây** (dòng 86–87).

**Bypass limit cho admin thế nào?** `app.py` dòng 141 chọn limiter theo `role` lấy từ JWT: `rate_limiter_admin if role == "admin" else rate_limiter_user`. Admin không được bỏ qua hoàn toàn mà dùng limiter riêng với hạn mức 100/phút. Muốn bỏ hẳn thì bỏ qua `limiter.check()` khi `role == "admin"`, nhưng nên giữ một giới hạn cao để token admin bị lộ không đốt được tiền vô hạn. Test: teacher gửi 20 request liên tiếp thì **20 × 200**, trong khi student bị chặn từ request thứ 11.

Vòng lặp 20 request bằng token student (trước đó đã gọi 1 request ở 4.2, nên còn 9 lượt):

```text
req  1 -> 200  {'requests_remaining': 8, ...}
req  2 -> 200  {'requests_remaining': 7, ...}
...
req  9 -> 200  {'requests_remaining': 0, ...}
req 10 -> 429  {'error': 'Rate limit exceeded', 'limit': 10, 'window_seconds': 60, 'retry_after_seconds': 58}
...
req 20 -> 429  {'error': 'Rate limit exceeded', 'limit': 10, 'window_seconds': 60, 'retry_after_seconds': 57}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795680512
retry-after: 57
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":57}}
```

Đúng 10 request được 200 trong một phút (1 + 9), từ request thứ 11 là 429. Header `Retry-After` cho client biết chờ bao lâu, và giảm dần (58 → 57) theo thời gian.

**Hạn chế (cần nhớ cho Part 6):** state nằm trong RAM của process.
- **Restart là mất.** Sau khi restart, student gọi lại được ngay (200) và `/me/usage` về `requests: 1`.
- **Chạy nhiều worker hoặc instance thì mỗi cái có bộ đếm riêng**, nên limit thực tế bị nhân lên N lần.

Part 6 yêu cầu rate limit lưu trong **Redis**, ví dụ sorted set: `ZREMRANGEBYSCORE` (bỏ cũ) → `ZCARD` (đếm) → `ZADD` (ghi) → `EXPIRE`.

### Exercise 4.4: Cost guard implementation

**Bản có sẵn (`production/cost_guard.py`):** đếm token theo user theo ngày (`UsageRecord`, đổi ngày thì tạo record mới, dòng 53–58), quy ra tiền theo giá GPT-4o-mini. Gọi `check_budget()` **trước** khi gọi LLM: vượt budget của user ($1/ngày) → **402 Payment Required**, vượt budget tổng ($10/ngày) → **503**, dùng tới 80% thì log warning. Gọi `record_usage()` **sau** khi có câu trả lời.

Với mock LLM, mỗi request chỉ tốn khoảng $0.00002 (`/me/usage`: 10 request = $0.000188). Muốn chạm $1 phải gửi khoảng 53.000 request, trong khi rate limit chỉ cho 10 request/phút. Vì vậy mình kiểm tra 402/503 bằng cách khởi tạo chính class `CostGuard` với budget rất nhỏ (không sửa file trong repo):

```text
CostGuard(daily_budget_usd=0.0001):
req 1: 200  used=$1.9e-05
...
req 5: 200  used=$9.3e-05          (log: "User student at 93% budget")
req 6: 200  used=$0.000112         ← đã VƯỢT budget nhưng vẫn được cho qua
req 7: 402  {'error': 'Daily budget exceeded', 'used_usd': 0.000112, 'budget_usd': 0.0001, 'resets_at': 'midnight UTC'}

CostGuard(global_daily_budget_usd=0.00005):
global req 1..3: 200
global req 4 (user4): 503  Service temporarily unavailable due to budget limits. Try again tomorrow.
```

**Lỗi mình thấy trong bản có sẵn:**
1. `check_budget` chỉ so số **đã tiêu** với budget (`>=`), không cộng chi phí của request sắp chạy. Request 6 vẫn lọt và làm tổng vượt budget.
2. `app.py` dòng 160 trả `"budget_remaining_usd": usage.total_cost_usd`, tức là số tiền **đã dùng** chứ không phải số **còn lại**. Response ghi `budget_remaining_usd: 1.9e-05`, trong khi `/me/usage` cùng lúc báo `budget_remaining_usd: 0.999812`.
3. State nằm trong RAM: restart mất số liệu (đã thấy ở 4.3), nhiều instance thì không chia sẻ được.

**Cách mình implement (`check_budget` với Redis, $10/user/tháng):**

- **Key theo user và theo tháng:** `budget:<user_id>:<YYYY-MM>` (giờ UTC). Sang tháng mới thì tự dùng key mới, nên "reset đầu tháng" không cần cron job. Đặt `EXPIRE` 32 ngày để key tháng cũ tự xoá.
- **Check và cộng tiền phải atomic.** Lời giải mẫu trong CODE_LAB làm `GET` → so sánh → `INCRBYFLOAT` thành 2 bước tách rời. Khi nhiều request đến cùng lúc, chúng cùng đọc thấy "còn tiền" rồi cùng cộng, nên budget bị vượt. Mình gộp cả 3 bước vào một **Lua script** chạy trên Redis. Redis chạy script nguyên khối, không xen lệnh khác vào giữa.
- **So `current + estimated_cost > budget`**, không so `current >= budget`, để request làm vượt budget bị chặn trước khi gọi LLM.

```python
import os
from datetime import datetime, timezone
import redis

MONTHLY_BUDGET_USD = 10.0
KEY_TTL_SECONDS = 32 * 24 * 3600

r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

_RESERVE_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost    = tonumber(ARGV[1])
local budget  = tonumber(ARGV[2])
if current + cost > budget then
  return 0
end
redis.call('INCRBYFLOAT', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""
_reserve = r.register_script(_RESERVE_LUA)


def check_budget(user_id: str, estimated_cost: float) -> bool:
    """True nếu còn budget (và đã trừ estimated_cost), False nếu vượt $10/tháng."""
    if estimated_cost < 0:
        raise ValueError("estimated_cost must be >= 0")
    key = f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"
    return _reserve(keys=[key], args=[estimated_cost, MONTHLY_BUDGET_USD, KEY_TTL_SECONDS]) == 1
```

Trong app: `if not check_budget(user, est): raise HTTPException(402, ...)` rồi mới gọi LLM.

**Test với Redis thật** (container `redis:7-alpine` tạm):

```text
== Test 1: tuần tự, cost $3/request, budget $10 ==
  request 1: check_budget('alice', 3.0) -> True    spent=$3.00
  request 2: check_budget('alice', 3.0) -> True    spent=$6.00
  request 3: check_budget('alice', 3.0) -> True    spent=$9.00
  request 4: check_budget('alice', 3.0) -> False   spent=$9.00
  request 5: check_budget('alice', 1.0) -> True    spent=$10.00  (9+1 = 10, đúng bằng budget → vẫn cho)
  request 6: check_budget('alice', 0.01) -> False  spent=$10.00
  user khác: check_budget('bob', 3.0) -> True      (budget tách theo user)
== Test 2: reset đầu tháng ==
  key tháng này: budget:alice:2026-11  TTL=2764800s (~32 ngày)
  sang 2026-12-01: check_budget('alice', 3.0) -> True  key mới: budget:alice:2026-12
== Test 3: 50 request đồng thời, mỗi request $0.5 (tối đa đúng 20 request = $10) ==
  atomic (Lua):  20 request được cho, spent=$10.00
  naive GET→INCR (tệ nhất trong 10 lần): 47 request được cho, spent=$23.50
```

Test 3 cho thấy cách làm 2 bước của CODE_LAB **chi lố $23.50 trên budget $10** khi có tải đồng thời. Bản Lua dừng đúng ở $10.00.

**Cải tiến khi dùng thật:** `estimated_cost` là ước lượng trước khi gọi LLM. Sau khi có số token thật, có thể cộng hoặc trừ phần chênh lệch (`INCRBYFLOAT key (actual − estimated)`). Có thể thêm cảnh báo khi dùng tới 80% và một budget tổng cho toàn hệ thống như bản có sẵn.

### Checkpoint 3

- **Rate limit bảo vệ tốc độ, cost guard bảo vệ tiền. Vì sao cần cả hai?** Rate limit giới hạn **số request trong một khoảng thời gian**, chống spam, DoS và một client chiếm hết tài nguyên. Nhưng nó không biết request đắt hay rẻ: 10 request/phút với prompt dài và output dài vẫn có thể tốn nhiều tiền, và 10 request/phút liên tục cả tháng là khoảng 430.000 request. Cost guard giới hạn **tổng tiền** theo ngày hoặc tháng, bất kể request đến nhanh hay chậm. Hai lớp bổ sung cho nhau: rate limit chặn các đợt tăng đột ngột, cost guard chặn chi phí cộng dồn.
- Mã lỗi khác nhau để client xử lý đúng: **401** chưa xác thực (cần key/token), **403** đã xác thực nhưng không có quyền, **429** gọi quá nhanh (chờ theo `Retry-After` rồi thử lại), **402** hết budget (thử lại cũng vô ích cho tới khi budget được reset).

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
| 4 | CP2: build image, đo kích thước, chạy Compose stack; xử lý lỗi Docker Hub `429 Too Many Requests` | Gợi ý pull base image qua `mirror.gcr.io` rồi `docker tag` lại tên gốc (không sửa Dockerfile); script build, đo size, test Compose | Số MB lấy trực tiếp từ `docker images` trên máy mình (1.67GB vs 240MB); tự kiểm cache bằng cách build lại thấy `CACHED`; tự xác nhận agent không mở cổng ra host (connection refused) và request đi qua nginx (header `Server: nginx`) |
| 5 | CP3: chạy Part 4 develop/production, giải thích JWT và rate limit, viết `check_budget` với Redis | Script curl test 401/403/200/429, decode payload JWT, script dùng `CostGuard` với budget nhỏ để thấy 402/503; gợi ý dùng Lua script cho atomic check+increment | Tự chạy vòng lặp 20 request thấy 429 từ request thứ 11; tự test `check_budget` với Redis thật: tuần tự, sang tháng mới, và 50 thread đồng thời (Lua dừng đúng $10.00, bản GET→INCR của CODE_LAB lố $23.50) |
