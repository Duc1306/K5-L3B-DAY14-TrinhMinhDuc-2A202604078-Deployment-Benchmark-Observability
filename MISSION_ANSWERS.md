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

> Lớp K5 deploy luôn **agent Part 6** (`06-lab-complete/`), không deploy app mẫu `03-cloud-deployment/` (README lưu ý số 8). Nền tảng chính: **Render**. Chi tiết trong [DEPLOYMENT.md](DEPLOYMENT.md).

### Exercise 3.2: Render deployment (nền tảng chính)

- **URL:** https://day14-agent-opr7.onrender.com
- **Cách deploy:** push repo lên GitHub → Render Dashboard → **New → Blueprint** → chọn repo → **Blueprint Path** = `06-lab-complete/render.yaml` → Apply. Render tạo 2 resource: web service `day14-agent` (Docker) và Key Value `day14-redis`. `REDIS_URL` được nối tự động qua `fromService`, còn `AGENT_API_KEY` do Render sinh (`generateValue: true`).
- **Screenshots:** [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

Tự kiểm theo CP6:

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     → 200   {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":138.3}
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      → 200   {"ready":true,"redis":"ok"}
$ curl ... -X POST "$BASE/ask" (không có key)                 → 401
$ curl ... -X POST "$BASE/ask" -H "X-API-Key: $AGENT_API_KEY"  → 200

Kết quả 4 lệnh (screenshots/test.png): 200, 200, 401, 200

$ python check_production_ready.py --public-url https://day14-agent-opr7.onrender.com
     ✅ Public URL: GET https://day14-agent-opr7.onrender.com/health → 200
```

`/ready` trả `redis: ok` → agent trên cloud nói chuyện được với Redis của Render. `"environment":"production"` → biến env của platform được áp dụng (và `/docs` trả 404 vì app ẩn docs khi production).

### Exercise 3.1: Railway — so sánh `railway.toml` với `render.yaml`

Mình không deploy thêm Railway mà so sánh hai file cấu hình (cả bản mẫu trong `03-cloud-deployment/` lẫn bản mình viết trong `06-lab-complete/`):

| Khía cạnh | `railway.toml` | `render.yaml` | Vì sao khác |
|-----------|----------------|---------------|-------------|
| Định dạng / phạm vi | TOML, cấu hình **build + deploy của một service** | YAML Blueprint, mô tả **cả hạ tầng**: nhiều service (web + Key Value), env var, cách nối giữa chúng | Railway coi file là "config-as-code" cho service; Render coi Blueprint là Infrastructure-as-Code cho cả project |
| Build | `builder = "DOCKERFILE"` (hoặc `NIXPACKS` / `RAILPACK` tự đoán, như bản mẫu) | `runtime: docker` + `dockerfilePath` / `dockerContext`, hoặc `runtime: python` + `buildCommand` (bản mẫu) | Hai nền tảng có builder tự động khác nhau |
| Lệnh chạy | `startCommand` (bản mẫu: `uvicorn app:app --port $PORT`); bản của mình bỏ trống → dùng `CMD` của Dockerfile | `startCommand` (bản mẫu); bản Docker của mình dùng `CMD` | Khi deploy bằng Docker, `CMD ... ${PORT:-8000}` là nguồn duy nhất, không lặp ở 2 chỗ |
| Health check | `healthcheckPath = "/health"` + `healthcheckTimeout` | `healthCheckPath: /health` | Cùng ý nghĩa: deploy chỉ "thành công" khi `/health` trả 200, và platform theo dõi tiếp sau đó |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 3` | Không có field, Render tự restart | Railway cho chỉnh policy, Render mặc định |
| Env var & secret | **Không** nằm trong file: đặt bằng `railway variables set ...` hoặc dashboard | Khai báo ngay trong file: `value:` cho giá trị thường, `generateValue: true` (tự sinh secret), `sync: false` (nhập tay trên dashboard), `fromService` (lấy từ service khác) | Render cho khai báo cả *tên* biến và nguồn của secret mà không lộ giá trị → dễ tái tạo môi trường |
| Redis | Tạo Redis trong project Railway rồi tham chiếu biến (`${{Redis.REDIS_URL}}`) trên dashboard | Khai báo `type: keyvalue` (tên cũ `redis`, như bản mẫu) trong cùng file và nối bằng `fromService ... property: connectionString` | Render gom mọi thứ vào một file |
| Monorepo (thư mục con) | `cd 06-lab-complete && railway up` (upload thư mục hiện tại) hoặc đặt Root Directory trong settings | `rootDir: 06-lab-complete` + chọn **Blueprint Path** khi tạo Blueprint | Render build từ GitHub nên cần biết thư mục con; Railway CLI upload thẳng thư mục local |
| Kích hoạt deploy | `railway up` từ máy, hoặc nối GitHub | Push lên GitHub → Render tự deploy (`autoDeploy`) | Render dựa trên Git, Railway có thêm luồng CLI |
| `PORT` | Railway inject `PORT` | Render inject `PORT` (mặc định 10000) | Cả hai đều yêu cầu app nghe theo `$PORT`, nên Dockerfile dùng `${PORT:-8000}` |

### Exercise 3.3: Cloud Run CI/CD (chỉ đọc, không deploy)

`03-cloud-deployment/production-cloud-run/cloudbuild.yaml` mô tả pipeline 4 bước, chạy tuần tự nhờ `waitFor` (một bước fail thì các bước sau không chạy):
1. **test**: trong `python:3.11-slim`, `pip install` rồi `pytest tests/`. Test fail thì dừng, không build.
2. **build**: `docker build` gắn tag `gcr.io/$PROJECT_ID/ai-agent:$COMMIT_SHA` và `:latest`, dùng `--cache-from ...:latest` để tận dụng layer cache.
3. **push**: đẩy cả 2 tag lên Container Registry.
4. **deploy**: `gcloud run deploy ai-agent --image ...:$COMMIT_SHA` ở `asia-southeast1`, `--min-instances=1` (tránh cold start), `--max-instances=10`, 512Mi / 1 CPU, `ENVIRONMENT=production`, và secret lấy từ **Secret Manager** (`--set-secrets`).

`service.yaml` là bản IaC (Knative) của cùng service: autoscale 1–10 instance, `containerConcurrency: 80`, giới hạn CPU/RAM, env và secret qua `secretKeyRef`, **liveness probe `/health`** và **startup probe `/ready`**, đúng phân biệt liveness/readiness ở Part 5.

*Nhận xét:* pipeline deploy theo tag `$COMMIT_SHA`, nên mỗi bản deploy gắn với một commit và rollback được về bản trước. Còn `service.yaml` trỏ `:latest`, không biết chính xác đang chạy bản nào. Repo mẫu cũng chưa có thư mục `tests/`, nên bước 1 sẽ fail nếu chạy nguyên trạng.

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

### Exercise 5.1: Health checks (`05-scaling-reliability/develop/app.py`)

Cách implement trong bản develop:

- **`GET /health` (liveness, dòng 104–144):** chỉ trả lời câu hỏi "process còn sống và phản hồi được không?". Trả 200 với `status`, `uptime_seconds`, `version`, `environment`, `timestamp` và một check nhẹ (RAM qua `psutil`: dùng ≥ 90% thì `degraded`). Endpoint này **không** kiểm tra dependency bên ngoài. Nếu Redis hay DB chết mà `/health` cũng fail, platform sẽ restart container liên tục dù restart không giúp gì.
- **`GET /ready` (readiness, dòng 147–168):** trả lời câu hỏi "có nên gửi traffic vào instance này không?". Cờ `_is_ready` chỉ bật sau khi `lifespan` startup xong (load model, kết nối dependency), và tắt ngay khi bắt đầu shutdown. Khi cờ tắt, endpoint trả **503**, để load balancer ngừng gửi request mới vào instance đang khởi động hoặc đang tắt.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.6,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:34:06.299918+00:00","checks":{"memory":{"status":"ok","used_percent":19.6}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

(`in_flight_requests: 1` là chính request `/ready`, vì middleware đếm cả nó.)

**Liveness khác readiness ở đâu?** Fail liveness thì platform **restart** container. Fail readiness thì load balancer **ngừng gửi traffic** nhưng không restart. Ví dụ khi Redis chết: `/health` vẫn phải 200 (process vẫn sống, restart không sửa được Redis), còn `/ready` phải 503 (instance không phục vụ được). Hợp đồng Part 6 yêu cầu đúng hành vi này.

**Vấn đề mình đo được:** `/ask` khai báo `async def` nhưng gọi mock LLM `ask()` có `time.sleep(3)`, một lời gọi **blocking**. Vì vậy nó chặn cả event loop:

```text
/health trả 200 sau 2.735327s     ← khi đang có 1 request /ask
/health khi rảnh: 200 sau 0.002575s
--- 3 request /ask đồng thời ---
  ask 1: 200 xong sau 3.016078s
  ask 3: 200 xong sau 9.059588s
  ask 2: 200 xong sau 9.059325s
  tổng: 9.21s                     ← 3 request bị xếp hàng (3 × 3s), không chạy song song
```

Khi có tải, liveness probe có thể **timeout** (vd probe timeout 1–2s) dù app không hỏng, và platform sẽ restart nhầm. Cách sửa: khai báo endpoint là `def` thường (FastAPI tự chạy trong threadpool), hoặc `await asyncio.to_thread(ask, q)`, hoặc dùng client LLM async.

### Exercise 5.2: Graceful shutdown

**Cách implement:**
1. **Ngừng nhận request mới:** uvicorn tự bắt SIGTERM và đóng listening socket. Bản develop còn đặt `_is_ready = False` trong phần shutdown của `lifespan`, để `/ready` trả 503.
2. **Hoàn thành request đang chạy:** `uvicorn.run(..., timeout_graceful_shutdown=30)` chờ request đang chạy xong (tối đa 30s). Middleware `track_requests` đếm `_in_flight_requests`, và `lifespan` chờ biến này về 0.
3. **Đóng kết nối:** phần sau `yield` trong `lifespan` là chỗ đóng Redis/DB pool và flush log.
4. **Thoát:** uvicorn thoát với exit code 0.

Trong hàm `shutdown_handler(signum, frame)` của đề, mình **không** tự gọi `sys.exit()`: làm vậy sẽ cắt ngang request đang chạy. Để uvicorn xử lý SIGTERM, còn phần dọn dẹp đặt trong `lifespan`.

Test: gửi `/ask` (mock LLM mất 3s), rồi 0.5s sau `kill -TERM <pid>`:

```text
  request MỚI gửi sau SIGTERM: bị từ chối (connection refused)
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
  → in-flight request: HTTP 200 sau 3.014213s
  process thoát với exit code 0 sau 3.25s kể từ SIGTERM
```

Log server:

```text
INFO:     Shutting down
INFO:     127.0.0.1:60318 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-26 15:34:21,827 INFO 🔄 Graceful shutdown initiated...
2026-11-26 15:34:21,827 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [745]
2026-11-26 15:34:21,829 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

→ **Request đang chạy hoàn thành (200)**, request mới bị từ chối, process thoát với exit code 0.

**Quan sát:**
- Dòng `Received signal 15` của `handle_sigterm` xuất hiện **cuối cùng**, sau `Finished server process`. Lúc chạy, `uvicorn.run()` cài handler SIGTERM của riêng nó, ghi đè `signal.signal(...)` ở dòng 186, và chỉ gọi lại handler cũ khi đã tắt xong. Vì vậy không thể dùng handler tự viết đó để "ngừng nhận request". Hook đúng chỗ là `lifespan`.
- Vòng chờ `_in_flight_requests` trong `lifespan` thực tế không phải chờ, vì uvicorn đã chờ request xong rồi mới chạy phần shutdown của `lifespan`. Vòng đó cũng dùng `time.sleep(1)` (blocking) bên trong hàm async.
- Trong Docker, cần `CMD` dạng exec để uvicorn là PID 1 và nhận được SIGTERM từ `docker stop`. Platform chỉ chờ một khoảng (Docker mặc định 10s) rồi gửi SIGKILL, nên timeout graceful phải nhỏ hơn khoảng đó.

### Exercise 5.3: Stateless design

**Anti-pattern:** lưu history trong biến toàn cục `conversation_history = {}`. Mỗi instance (process hoặc container) có RAM riêng, nên load balancer gửi request sang instance khác là mất ngữ cảnh. Restart hoặc deploy cũng mất sạch.

**Refactor (đã có trong `production/app.py`):** state chuyển ra Redis, instance không giữ gì trong RAM:

```python
def save_session(session_id: str, data: dict, ttl_seconds: int = 3600):
    _redis.setex(f"session:{session_id}", ttl_seconds, json.dumps(data))   # TTL 1h, tự dọn session cũ

def load_session(session_id: str) -> dict:
    data = _redis.get(f"session:{session_id}")
    return json.loads(data) if data else {}

def append_to_history(session_id, role, content):
    session = load_session(session_id)
    history = session.get("history", [])
    history.append({"role": role, "content": content, "timestamp": ...})
    session["history"] = history[-20:]        # giữ tối đa 20 message
    save_session(session_id, session)
```

Client gửi `session_id`, instance nào nhận request cũng đọc được history từ Redis.

**Thí nghiệm chứng minh state trong RAM hỏng khi scale:** chạy 2 instance `production/app.py` **không có Redis** (app tự rơi về in-memory), rồi gửi xen kẽ 4 câu cùng một session:

```text
  Q1 → :8101  served_by= inst-8101  turn= 2  storage= in-memory
  Q2 → :8102  served_by= inst-8102  turn= 2  storage= in-memory
  Q3 → :8101  served_by= inst-8101  turn= 3  storage= in-memory
  Q4 → :8102  served_by= inst-8102  turn= 3  storage= in-memory
  history trên :8101 → 4 messages: ['Q1', 'Q3']
  history trên :8102 → 4 messages: ['Q2', 'Q4']
```

→ Cùng một session nhưng **history bị chia đôi** giữa 2 instance, mỗi bên chỉ thấy một nửa cuộc trò chuyện. Có Redis thì cả 5 câu nằm trọn ở một chỗ (xem 5.5).

**Điểm chưa ổn mình thấy trong `production/app.py`:**
- **Âm thầm rơi về in-memory:** Redis không kết nối được lúc import thì app chỉ `print` cảnh báo rồi chạy với RAM, và `/ready` **vẫn trả 200** (đã thử: `/ready khi KHÔNG có Redis: 200`). Load balancer sẽ gửi traffic vào một instance làm hỏng session. Với app cần scale, thiếu Redis thì `/ready` phải trả 503 (đúng như hợp đồng Part 6).
- **`append_to_history` không atomic:** GET → sửa → SET. Hai request cùng session đến đồng thời ở 2 instance có thể ghi đè nhau và mất message. Cách tốt hơn là dùng list của Redis: `RPUSH history:<sid> msg` + `LTRIM history:<sid> -20 -1` + `EXPIRE`. Mỗi lệnh atomic, gom vào một pipeline `MULTI`.
- **`turn` lệch 1:** câu đầu tiên báo `turn: 2`, vì code đếm history **sau khi** đã thêm câu hỏi hiện tại rồi còn `+ 1`. Khi history bị cắt còn 20 message, `turn` dừng ở 11.

### Exercise 5.4: Load balancing

Chạy trong `05-scaling-reliability/production/` (README lưu ý số 7):

```bash
touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 8 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 7 seconds              0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 20 seconds (healthy)   6379/tcp
```

3 agent đều healthy sau 24s, và chỉ nginx publish cổng ra ngoài (8080). Gửi 10 request `POST /chat` qua `http://localhost:8080`:

```text
req  1  HTTP 200  X-Served-By=172.18.0.5:8000  served_by=instance-f41fd5
req  2  HTTP 200  X-Served-By=172.18.0.4:8000  served_by=instance-c1a364
req  3  HTTP 200  X-Served-By=172.18.0.3:8000  served_by=instance-898253
req  4  HTTP 200  X-Served-By=172.18.0.5:8000  served_by=instance-f41fd5
req  5  HTTP 200  X-Served-By=172.18.0.4:8000  served_by=instance-c1a364
req  6  HTTP 200  X-Served-By=172.18.0.3:8000  served_by=instance-898253
req  7  HTTP 200  X-Served-By=172.18.0.5:8000  served_by=instance-f41fd5
req  8  HTTP 200  X-Served-By=172.18.0.4:8000  served_by=instance-c1a364
req  9  HTTP 200  X-Served-By=172.18.0.3:8000  served_by=instance-898253
req 10  HTTP 200  X-Served-By=172.18.0.5:8000  served_by=instance-f41fd5

$ docker compose -p k5-day14-scaling logs agent | grep 'POST /chat' | ...   # đếm theo instance
      4 agent-1
      3 agent-2
      3 agent-3
```

→ Nginx chia **round-robin** đều cho 3 instance theo vòng .5 → .4 → .3. Header `X-Served-By` (`$upstream_addr` trong `nginx.conf`) và field `served_by` khớp nhau.

**Cách hoạt động:** `upstream agent_cluster { server agent:8000; }`. DNS nội bộ của Docker trả về IP của cả 3 container cho tên `agent`, và nginx quay vòng giữa chúng. `proxy_next_upstream error timeout http_503` + `proxy_next_upstream_tries 3`: instance lỗi thì nginx tự thử sang instance khác. Config không dùng tham số `resolve` cho `server`, nên nginx chỉ resolve tên `agent` lúc khởi động. Scale thêm instance sau đó thì phải reload nginx mới thấy.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 1e58a17c-4b64-44a5-a1dc-e9537ded534a

Request 1: [instance-c1a364]   Q: What is Docker?
Request 2: [instance-898253]   Q: Why do we need containers?
Request 3: [instance-f41fd5]   Q: What is Kubernetes?
Request 4: [instance-c1a364]   Q: How does load balancing work?
Request 5: [instance-898253]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-898253', 'instance-c1a364', 'instance-f41fd5'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của cùng một session đi qua **cả 3 instance**, nhưng history vẫn đủ **10 message** (5 user + 5 assistant), vì instance nào cũng đọc và ghi cùng key `session:<id>` trong Redis (`TTL = 3600s`). So với thí nghiệm in-memory ở 5.3, nơi history bị chia đôi.

`test_stateless.py` không tự tắt instance nào, nên mình làm thêm 2 thí nghiệm:

**A. `docker kill` 1 instance (chết đột ngột) rồi gọi tiếp cùng session:**

```text
đã kill k5-day14-scaling-agent-1 (172.18.0.5)
Q6  → HTTP 200 sau   0.11s  X-Served-By=172.18.0.4:8000                   served_by=instance-c1a364
Q7  → HTTP 200 sau   0.14s  X-Served-By=172.18.0.3:8000                   served_by=instance-898253
Q8  → HTTP 200 sau  60.08s  X-Served-By=172.18.0.5:8000, 172.18.0.4:8000  served_by=instance-c1a364
Q9  → HTTP 200 sau   0.14s  X-Served-By=172.18.0.4:8000                   served_by=instance-c1a364
Q10 → HTTP 200 sau   0.12s  X-Served-By=172.18.0.3:8000                   served_by=instance-898253
Q11 → HTTP 200 sau   0.14s  X-Served-By=172.18.0.4:8000                   served_by=instance-c1a364
--- history sau khi kill ---
count = 20
user messages: ['Why do we need containers?', 'What is Kubernetes?', 'How does load balancing work?', 'What is Redis used for?', 'After kill Q6', ..., 'After kill Q11']
nginx: upstream timed out (110: Operation timed out) while connecting to upstream → http://172.18.0.5:8000/chat
```

- **Không request nào lỗi và conversation vẫn còn nguyên:** 6 request sau khi kill đều 200, history có đủ câu cũ lẫn câu mới. Câu `What is Docker?` không còn là do giới hạn 20 message, không phải do mất dữ liệu.
- **Tới lượt instance đã chết (Q8), nginx mất 60.08s mới chuyển sang instance khác.** Header `X-Served-By: 172.18.0.5:8000, 172.18.0.4:8000` cho thấy nginx thử `.5` trước, bị timeout, rồi mới thử lại sang `.4`. Thời gian chờ 60s chính là `proxy_connect_timeout` mặc định của nginx. Sau đó nginx đánh dấu `.5` là hỏng (`max_fails=1`, `fail_timeout=10s` mặc định) nên Q9–Q11 không bị chậm nữa. **Cải tiến:** đặt `proxy_connect_timeout 2s;` và `max_fails`/`fail_timeout` rõ ràng. Người dùng thật sẽ không chờ 60s.
- `docker kill` được Docker coi là **dừng thủ công**, nên `restart: unless-stopped` không bật lại: `exit=137` (= 128 + SIGKILL), `restarts=0`.

**B. Process trong container tự thoát (mô phỏng crash), cho thấy tác dụng của `restart: unless-stopped`:**

```text
trước: k5-day14-scaling-agent-2 restarts=0
sau 8s: k5-day14-scaling-agent-2 running restarts=1
agent-2  | INFO:     Shutting down
agent-2  | INFO:app:Instance instance-898253 shutting down
agent-2  | INFO:app:Starting instance instance-db33ae
bật lại k5-day14-scaling-agent-1 thủ công (docker start): healthy   ← thành instance-427cd3
--- 6 request sau khi bật lại đủ 3 instance (cùng session) ---
Q12 → HTTP 200  served_by=instance-c1a364
Q13 → HTTP 200  served_by=instance-db33ae
Q14 → HTTP 200  served_by=instance-c1a364
Q15 → HTTP 200  served_by=instance-427cd3
...
history count = 20
```

Docker tự restart container (`restarts=1`). Instance mới có **ID khác** (`instance-db33ae`) nhưng vẫn phục vụ tiếp session cũ, vì state không nằm trong instance. Dọn stack: `docker compose -p k5-day14-scaling down`.

### Checkpoint 4

- **Vì sao state trong RAM hỏng khi chạy nhiều instance?** Mỗi instance là một process riêng với RAM riêng. Load balancer gửi các request của cùng một user tới các instance khác nhau, nên mỗi instance chỉ thấy một phần state. Thí nghiệm 5.3 cho thấy history bị chia đôi `['Q1','Q3']` / `['Q2','Q4']`. State cũng mất khi instance restart hoặc bị thay trong lúc deploy hay autoscale. Đưa state ra Redis thì mọi instance như nhau: thêm, bớt hay kill instance nào cũng được (thí nghiệm A và B).
- **Load balancing với Nginx:** một điểm vào duy nhất (`:8080`), round-robin giữa các instance, tự thử instance khác khi một instance lỗi (`proxy_next_upstream`). Cần đặt timeout ngắn để failover nhanh.

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản **đầy đủ** (có Docker) trong `06-lab-complete/`, trên WSL2 (`os.name == "posix"`):

```text
$ python check_production_ready.py
...
✅ 1. Dockerfile (multi-stage, < 500 MB)
     ✅ Docker: build image thành công — 39s
     ✅ Docker: image < 500 MB — 63 MB
     ✅ Docker: container chạy được, GET /health → 200
...
✅ 7. Rate limiting
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ 8. Cost guard
     ✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
...
✅ 10. Structured logging
     ✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 15 dòng JSON
✅ 11. Graceful shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.8s
...
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

### Cách mình làm từng tiêu chí

| # | Tiêu chí | File | Cách làm và lý do |
|---|----------|------|--------------------|
| 1 | Dockerfile | `Dockerfile` | 2 stage. `builder` cài dependency vào venv `/opt/venv`. `runtime` là `python:3.11-slim` sạch, chỉ copy venv + `app/` + `utils/`, user `app` (non-root), `HEALTHCHECK` gọi `/health` theo `$PORT`. `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000} ..."]`: `exec` để uvicorn là PID 1 và nhận SIGTERM. Đã có wheel sẵn cho mọi package nên không cần gcc. Image 63 MB (`docker image inspect`) / 286 MB (`docker images`). |
| 2 | Compose | `docker-compose.yml` | `agent` build từ Dockerfile + `redis:7-alpine` có healthcheck. `depends_on: condition: service_healthy`. `REDIS_URL=redis://redis:6379/0` ghi đè giá trị trong `.env`. `env_file` với `required: false` nên không có `.env` vẫn chạy. |
| 3 | `.dockerignore` | `.dockerignore` | Loại `.env*` (giữ `.env.example`), `__pycache__`, `venv`, `.git`, tài liệu, script chấm và `benchmark-observability/`. |
| 4 | `/health` | `app/main.py` | Luôn 200, **không** gọi Redis. Redis chết thì restart agent cũng không sửa được, nên không được làm fail liveness. |
| 5 | `/ready` | `app/main.py`, `app/redis_client.py` | `PING` Redis với `socket_connect_timeout=1`. Lỗi thì trả **503** `{"ready":false,"redis":"unreachable"}`. Client Redis tạo bằng `redis.from_url()`, lệnh này chưa kết nối ngay, nên app vẫn khởi động được khi chưa có Redis. |
| 6 | API key | `app/auth.py` | `APIKeyHeader("X-API-Key", auto_error=False)` + `secrets.compare_digest` (chống timing attack). Thiếu hoặc sai key đều **401** (khác bản Part 4 trả 403 khi sai key). Chưa đặt `AGENT_API_KEY` thì từ chối mọi request. Khi `ENVIRONMENT=production` mà thiếu key thì app không khởi động. |
| 7 | Rate limit | `app/rate_limiter.py` | Sliding window log bằng sorted set trong Redis, key `ratelimit:<sha256(api_key)[:16]>` (không lưu key thô). `ZREMRANGEBYSCORE → ZCARD → ZADD → PEXPIRE` nằm trong **một Lua script** nên atomic giữa 2 worker và nhiều instance. Dùng `TIME` của Redis để mọi instance chung một đồng hồ. Vượt giới hạn thì **429** + `Retry-After`. |
| 8 | Cost guard | `app/cost_guard.py` | Chi phí theo token (giá gpt-4o-mini), cộng dồn theo **ngày và tháng** (UTC) trong Redis. Trước khi gọi LLM, Lua script kiểm tra `đã tiêu + ước lượng > budget` và **giữ chỗ** atomic (bài học từ Exercise 4.4: tách GET → INCR sẽ vượt budget khi có tải đồng thời). Sau khi gọi, cộng hoặc trừ phần chênh với chi phí thật. LLM lỗi thì trả lại phần giữ chỗ. Vượt budget thì **402**. |
| 9 | Config | `app/config.py`, `.env.example` | Mọi giá trị đọc từ env (`PORT`, `ENVIRONMENT`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`, `LOG_LEVEL`, ...). Ép kiểu và báo lỗi rõ ràng (vd `RATE_LIMIT_PER_MINUTE phải là số nguyên`). Secret không có giá trị mặc định. |
| 10 | Log JSON | `app/logging_config.py` | `JsonFormatter` dựng dict rồi `json.dumps` **một lần**, nên mỗi dòng là một JSON object hợp lệ (tránh lỗi đã thấy ở Part 1). Logger `agent` có handler riêng và `propagate=False`, không bị `basicConfig` hay root logger chen vào. Middleware ghi `request_id`, `method`, `path`, `status`, `ms`. **Không** log API key hay nội dung câu hỏi (chỉ log `question_chars`). |
| 11 | Graceful shutdown | `app/main.py` (lifespan) | **Không** tự đăng ký signal handler (Part 5 cho thấy uvicorn ghi đè nó). Uvicorn nhận SIGTERM, ngừng nhận kết nối mới và chờ request đang chạy xong. Sau đó phần shutdown của `lifespan` ghi log và đóng Redis. `--timeout-graceful-shutdown 8` nhỏ hơn 10s mặc định của `docker stop`. |
| 12 | Deploy config | `railway.toml`, `render.yaml` | Railway: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`. Render: `type: web`, `runtime: docker`, `healthCheckPath: /health`, `AGENT_API_KEY` dùng `generateValue: true`, Redis khai báo là service `keyvalue` và nối vào `REDIS_URL` qua `fromService`. |

Ngoài ra, `/ask` khai báo là `def` (không phải `async def`) nên FastAPI chạy nó trong threadpool. Lời gọi LLM và Redis blocking không chặn event loop (tránh lỗi đã đo ở Exercise 5.1).

### Kiểm tra thêm bằng `docker compose` (ngoài script chấm)

`docker compose -p k5-day14-agent up -d --build` với `.env` tạm (key ngẫu nhiên, `RATE_LIMIT_PER_MINUTE=10`), xoá `.env` sau khi test:

```text
$ GET /health                → 200 {"status":"ok","version":"1.0.0","environment":"staging","uptime_seconds":0.7}
$ GET /ready                 → 200 {"ready":true,"redis":"ok"}
$ POST /ask (không có key)   → 401 {"detail":"Missing or invalid API key. Send header: X-API-Key: <key>"}
$ POST /ask (sai key)        → 401 (như trên)
$ POST /ask (đúng key)       → 200 x-ratelimit-limit: 10, x-ratelimit-remaining: 9
  {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":5,"output_tokens":20,"cost_usd":1.275e-05,"requests_remaining_this_minute":9}}
```

**Xử lý lỗi: input sai trả mã lỗi rõ ràng, không lộ stack trace:**

```text
question rỗng          → 422 {"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character",...}]}
body không phải JSON   → 422 {"detail":[{"type":"json_invalid","loc":["body",0],"msg":"JSON decode error",...}]}
thiếu field question   → 422 {"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required",...}]}
```

Lỗi không lường trước (test bằng `TestClient`, cho `count_tokens` ném `ZeroDivisionError("secret internal detail")`):

```text
status: 500
body: {"error":"internal_error","request_id":"253db989b2fc4cff"}
stack trace / chi tiết lỗi trong response? False
log: {"ts": "...", "lvl": "ERROR", "logger": "agent", "event": "unhandled_error", "request_id": "253db989b2fc4cff", "method": "POST", "path": "/ask", "exc_type": "ZeroDivisionError", "exc": "Traceback (most recent call last):\n ..."}
```

→ Client chỉ nhận thông báo chung kèm `request_id`. Stack trace nằm trong **một** dòng log JSON có cùng `request_id`, nên vẫn tra được lỗi.

**Rate limit** (limit 10, đã dùng 1 request; 3 request bị 422 không bị tính): `200 × 9` rồi `429 × 3`, kèm `retry-after: 59`, `x-ratelimit-remaining: 0`.

**`/health` khi đang có 5 `/ask` chạy song song:** `200 sau 0.008s`. Event loop không bị chặn.

**Redis chết** (`docker compose stop redis`): `/health → 200`, `/ready → 503 {"ready":false,"redis":"unreachable"}`, `/ask → 503 {"detail":"Service temporarily unavailable"}`. Bật lại Redis thì `/ready → 200`. *Ghi chú:* request `/ask` đầu tiên lúc Redis vừa chết mất 7.8s mới trả 503, dù `socket_connect_timeout=1`. Có thể do tên `redis` không còn trong DNS của Docker, phải hỏi ra DNS ngoài, mà timeout kết nối không bao gồm thời gian tra DNS.

**Log JSON mẫu** (trích `docker compose logs agent`). Không dòng nào chứa API key (`grep` key → 0 dòng):

```json
{"ts": "2026-11-26T09:11:01.316+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "23438b0b70114307", "method": "POST", "path": "/ask", "status": 429, "ms": 7.9}
{"ts": "2026-11-26T09:11:17.539+00:00", "lvl": "ERROR", "logger": "agent", "event": "dependency_error", "dependency": "redis", "path": "/ask"}
{"ts": "2026-11-26T09:11:17.540+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "request_id": "f99fc86e37b14f4f", "method": "POST", "path": "/ask", "status": 503, "ms": 7850.2}
```

**`docker compose stop agent`** → exit code 0 sau 0.7s.

## AI log

| # | Hỏi gì | Nhận gì | Tự sửa / tự kiểm tra thế nào |
|---|--------|---------|-------------------------------|
| 1 | Nhờ Claude Code đọc README, RULES, CHECKPOINTS, SUBMISSION, RUBRIC và tóm tắt yêu cầu lab | Bản tóm tắt thang điểm, luật, lịch CP0–CP8 và các TODO của Part 6 | Tự đối chiếu lại với RUBRIC.md (tổng 120, Part 6 = 60) |
| 2 | CP0: kiểm tra môi trường | Phát hiện Docker Desktop chưa bật và WSL thiếu `pip`/`venv`; cài `python3.12-venv`, `python3-pip`; tạo venv `~/.venvs/day14` | `python -c "import os; print(os.name)"` in `posix`; `docker run --rm hello-world` chạy được trong WSL; `check_production_ready.py --static-only` chạy được (0/12 như kỳ vọng với bản khung) |
| 3 | CP1: tìm anti-pattern, chạy bản develop/production, điền bảng so sánh | Danh sách anti-pattern kèm số dòng, script curl chạy hai bản, phát hiện lỗi logging của bản production | Tự chạy lại curl và xem log; tự kiểm lỗi logging bằng cách in root logger level sau `import app` và thử `json.loads` dòng log |
| 4 | CP2: build image, đo kích thước, chạy Compose stack; xử lý lỗi Docker Hub `429 Too Many Requests` | Gợi ý pull base image qua `mirror.gcr.io` rồi `docker tag` lại tên gốc (không sửa Dockerfile); script build, đo size, test Compose | Số MB lấy trực tiếp từ `docker images` trên máy mình (1.67GB vs 240MB); tự kiểm cache bằng cách build lại thấy `CACHED`; tự xác nhận agent không mở cổng ra host (connection refused) và request đi qua nginx (header `Server: nginx`) |
| 5 | CP3: chạy Part 4 develop/production, giải thích JWT và rate limit, viết `check_budget` với Redis | Script curl test 401/403/200/429, decode payload JWT, script dùng `CostGuard` với budget nhỏ để thấy 402/503; gợi ý dùng Lua script cho atomic check+increment | Tự chạy vòng lặp 20 request thấy 429 từ request thứ 11; tự test `check_budget` với Redis thật: tuần tự, sang tháng mới, và 50 thread đồng thời (Lua dừng đúng $10.00, bản GET→INCR của CODE_LAB lố $23.50) |
| 6 | CP4: test health/ready, graceful shutdown, chạy stack 3 instance + test stateless; xử lý WSL timeout do thiếu RAM | Script đo `/health` khi có request đang chạy, test SIGTERM giữa request, thí nghiệm 2 instance không Redis, thí nghiệm kill/crash instance; chẩn đoán RAM host chỉ còn ~700MB → `wsl --shutdown`, tắt bớt app | Tự thấy `/health` chậm 2.7s và 3 request xếp hàng 9.2s (event loop bị chặn); in-flight request vẫn 200 sau SIGTERM, exit 0; history bị chia đôi khi không có Redis; `test_stateless.py` 5 câu qua 3 instance vẫn đủ 10 message; kill 1 instance → request vẫn 200 nhưng có 1 request chờ 60s (proxy_connect_timeout mặc định) |
| 7 | CP5: hoàn thành các TODO của `06-lab-complete/` theo hợp đồng của `check_production_ready.py` | Claude đọc script chấm, viết `config.py`, `logging_config.py`, `auth.py`, `redis_client.py`, `rate_limiter.py` (Lua sliding window), `cost_guard.py` (Lua giữ chỗ budget ngày + tháng), `main.py`, Dockerfile multi-stage, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Chạy `--static-only` (28/28) → `--skip-docker` (39/39) → bản đầy đủ **PASS 12/12, 43/43**; tự test thêm bằng `docker compose`: 401/200/429/422, Redis chết → `/ready` 503 còn `/health` 200, log không chứa API key, `docker stop` exit 0. **Cần tự đọc lại và giải thích được từng file trong `app/`** (Lab Coach có thể hỏi) |
| 8 | CP6: deploy Render, viết `DEPLOYMENT.md`, so sánh `railway.toml` với `render.yaml`, đọc pipeline Cloud Run | Claude kiểm tra tài liệu Render (đường dẫn tương đối theo `rootDir`, Blueprint Path, `keyvalue`, `generateValue`), hướng dẫn tạo Blueprint, soạn `DEPLOYMENT.md` và bảng so sánh | Tự tạo Blueprint trên Render và lấy key; Claude gọi URL public: `/health` 200, `/ready` 200 (`redis: ok`), `/ask` không key và sai key 401; `check_production_ready.py --public-url` → ✅. Lệnh có key tự chạy (không đưa key cho AI) |
| 9 | CP7: chạy Part 7 Bước 1 (Exercise 7.1–7.3), điền `BENCHMARK_REPORT.md` mục 1–3 | Claude viết script điều phối chạy agent v1/v2 + `load_test.py` + `run_eval.py` đúng lệnh trong CHECKPOINTS; thêm metric `agent_question_length_chars`; đề xuất 2 entry `KNOWLEDGE_BASE`; viết `.github/workflows/quality-gate.yml`; giải thích số liệu | Mọi số trong `BENCHMARK_REPORT.md` lấy từ 10 file JSON trong `reports/` (đã đối chiếu lại bằng script đọc JSON); tự `grep` từ khoá KB mới trong golden dataset để chắc không regression; eval cuối `✅ PASS` 23/23; tự đo riêng hiện tượng `localhost` → `::1` chậm 2.02s trên Windows trước khi ghi vào báo cáo |
| 10 | CP8: chạy stack observability, Locust scale 1 vs 3, thí nghiệm alert A–E, chụp 4 screenshot, điền `BENCHMARK_REPORT.md` mục 4–6 | Claude chẩn đoán agent crash (`No module named 'uvicorn'` → thêm `ENV PYTHONUSERBASE` vào `production/Dockerfile`), phát hiện `docker compose run` tự scale lại agent (→ `--no-deps`), viết script đo thời gian alert qua Prometheus API, lấy trace qua Jaeger API v3, chụp Grafana / Jaeger / Prometheus bằng Edge headless; đề xuất chạy thí nghiệm ở 15 user | Tự kiểm số instance trước/sau mỗi lần Locust chạy; tự tính chi phí nền theo số user từ số liệu thật trước khi đổi 30 → 15 user; đối chiếu mọi số trong báo cáo với `cp8_results.json`, CSV Locust và log Nginx (16 × 504 từ instance bị pause); xem lại 4 ảnh (2 alert cùng `FIRING`); laptop ngủ giữa chừng làm hỏng lần chạy C/E → chạy lại riêng C/E với chế độ giữ máy thức |
