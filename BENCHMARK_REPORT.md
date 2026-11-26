# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong `reports/` (bản gốc ở `06-lab-complete/benchmark-observability/develop/reports/`) và CSV / screenshot thật, không ước lượng.

**Họ tên:** Trinh Minh Duc — 2A202604078
**Ngày chạy:** 26/11/2026
**Môi trường:** Laptop Windows 11, Intel Core i5-11300H (4 nhân / 8 luồng), RAM 8 GB. Phần develop (mục 2–3) chạy bằng **Python 3.12.10 trên Windows**, agent và công cụ đo chạy trên cùng một máy. Lý do không dùng WSL: lúc đo, WSL2 liên tục báo `HCS_E_CONNECTION_TIMEOUT` vì máy thiếu RAM. Phần develop không cần SIGTERM hay Docker nên chạy trên Windows không làm sai kết quả. Docker Desktop 28.0.4 (backend WSL2) dùng cho mục 4–5.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | Một lần gọi LLM (mock) mất khoảng 120–180 ms, cộng retrieval khoảng 20 ms, nên p50 đo được khoảng 200 ms. 500 ms để dư khoảng 2.5 lần cho jitter và hàng đợi khi có tải, mà vẫn đủ nhanh cho chat tương tác. Đặt theo **p95**, không theo average (xem 2.2). |
| Error rate `/ask` | < **2** % | Mock mô phỏng upstream LLM lỗi khoảng 1% (`MOCK_ERROR_RATE=0.01`), đo được 0.67–1.67%. Error budget 2% chừa chỗ cho lỗi upstream bình thường; vượt 2% nghĩa là có sự cố thật. |
| Accuracy trên golden dataset | ≥ **85** % | Dùng làm quality gate trong CI (`--min-accuracy 0.85`), kèm điều kiện **không regression** so với baseline. Bản cuối đạt 100%. Gate để 85% (không phải 100%) để dataset hoặc chấm điểm theo keyword thay đổi nhỏ không chặn mọi PR, còn regression thì luôn bị chặn. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

`python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (`MOCK_SLOW_RATE` mặc định 0.03)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.03 | 201.5 | 238.6 | 1718.2 | 0.67% (2× 503) | ✅ PASS |
| 10 | 36.49 | 190.6 | 432.4 | 1690.8 | 0.67% (2× 503) | ✅ PASS |
| 50 | 94.25 | 220.0 | 540.0 | 1728.4 | 1.67% (5× 503) | ❌ FAIL (p95) |
| 100 | 46.28 | 795.1 | 4367.7 | 4854.7 | 0.67% (2× 503) | ❌ FAIL (p95) |

**Nhận xét:** RPS **không** tăng tuyến tính theo concurrency.

- **1 → 10:** gần tuyến tính (×9.1). Theo định luật Little, `RPS ≈ concurrency / latency`. Latency gần như không đổi (p50 khoảng 200 ms) vì agent là async (`await asyncio.sleep` trong mock LLM), nên 10 request chờ LLM cùng lúc không chặn nhau. 10 / 0.233 s (avg) ≈ 43 RPS lý thuyết, đo được 36.5.
- **10 → 50:** chỉ tăng ×2.6 (lý thuyết 50 / 0.32 s ≈ 155 RPS, đo được 94). p95 vượt 500 ms. Agent chạy **1 process uvicorn, 1 event loop**. Phần việc tốn CPU của mỗi request (Pydantic, ghi log JSON ra stdout, cập nhật Prometheus) chạy tuần tự trên một core, nên request bắt đầu xếp hàng. Trên laptop này, `load_test.py` còn chạy cùng máy và tranh CPU với agent.
- **50 → 100:** RPS **giảm** còn 46 trong khi p50 lên 795 ms và p99 gần 4.9 s, tức là đã quá tải. Thêm request song song chỉ làm hàng đợi dài ra. Lần chạy này cũng chỉ có 300 request cho 100 worker (mỗi worker khoảng 3 request, kéo dài 6.5 s), nên chi phí mở 100 kết nối chiếm phần lớn. Muốn số ổn định hơn ở mức này nên chạy theo thời gian (`--duration 30`).
- **Kết luận:** điểm bão hoà của 1 instance trên máy này nằm giữa concurrency 10 và 50. Muốn chịu tải cao hơn thì scale ngang (nhiều worker hoặc instance, xem mục 5), không phải đẩy thêm concurrency vào một process.

### 2.2 Tail latency

`MOCK_SLOW_RATE=<s> python app.py` (restart v2 mỗi lần) + `load_test.py --requests 300 --concurrency 20 --out reports/load_v2_slow_<s>.json`

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 211.7 | 492.3 | 504.8 |
| 0.03 (mặc định) | 253.2 | 505.6 | 1694.8 |
| 0.1 | 389.5 | 1700.1 | 1738.1 |

**Nhận xét:**

- **p99 nhạy nhất.** Từ 0 lên 0.03 (3% request chậm thêm 1.5 s), **avg chỉ tăng 41.5 ms (+20%)** nhưng **p99 tăng từ 504.8 lên 1694.8 ms (×3.4)**. 3% request chậm nằm gọn trong 1% cuối, nên p99 "bắt" được ngay, còn average thì pha loãng chúng với 97% request nhanh.
- **p95 chỉ nhảy khi tỉ lệ chậm vượt 5%.** Ở 0.03, p95 gần như không đổi (492 → 506 ms). Ở 0.1 (10% chậm > 5%), p95 lên **1700.1 ms**. Average lúc đó cũng chỉ 389.5 ms, vẫn "trông ổn" so với SLO 500 ms dù 1/10 người dùng chờ gần 2 giây.
- **Vì sao SLO không đặt theo average:** average che giấu tail latency. Một SLO "avg < 500 ms" vẫn PASS ở cả 3 mức (211 / 253 / 389 ms) trong khi trải nghiệm của 3–10% người dùng rất tệ. SLO theo p95/p99 trả lời đúng câu hỏi "người dùng thứ 95/99 trên 100 phải chờ bao lâu".
- Ngay cả khi `MOCK_SLOW_RATE=0`, p95 đã là 492.3 ms (p90 = 236.7 ms) ở concurrency 20, sát ngưỡng 500 ms. Khoảng 5% request bị chậm thêm khoảng 250 ms vì xếp hàng trên máy local, nên SLO 500 ms ở mức tải này đã khá sát. Đây cũng là lý do lần chạy 0.03 FAIL p95 chỉ vì 5.6 ms.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100% (23/23)** |
| Avg tokens / câu | 57.0 | 109.1 | 116.7 |
| Tổng cost (USD) | 0.000403 | 0.000762 | 0.000816 |
| Cost / câu đúng (USD) | 0.00040275 | 0.00003627 | 0.00003550 |
| Quality gate | ❌ FAIL (≥ 80%) | ✅ PASS (≥ 80%, 0 regression so với v1) | ✅ PASS (≥ 100%, 0 regression so với v2) |

Lệnh cuối: `python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` → `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`, `Câu được cải thiện : 2`, `Câu bị regression : 0`.

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | `KNOWLEDGE_BASE` không có tài liệu nào khớp nên v2 trả câu chung chung. Thêm entry từ khoá `["jwt", "api key"]`: *"API key là chuỗi bí mật cố định, không tự hết hạn... JWT là token có chữ ký số, chứa thông tin user, role và thời điểm hết hạn (exp)..."* |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Thêm entry từ khoá `["kubernetes", "hpa", "autoscal"]`: *"Kubernetes HPA... tự tăng giảm số pod dựa trên mức dùng CPU, memory hoặc custom metrics như RPS hay độ dài hàng đợi..."* |

Cách tránh regression: trước khi thêm, mình `grep` các từ khoá mới trong `golden_dataset.jsonl`. `jwt` / `api key` chỉ xuất hiện ở `security-02`, `kubernetes` / `hpa` chỉ ở `scaling-03`, nên retrieval của 21 câu còn lại không đổi. **Không sửa đáp án** (`golden_dataset.jsonl`), chỉ sửa tài liệu. Đã chép cùng thay đổi sang `production/utils/mock_llm.py` để stack Docker ở mục 4 cũng dùng KB mới.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chất lượng:** v2 (RAG) tăng accuracy từ 4.3% lên 91.3% (100% sau khi bổ sung tài liệu).
- **Chi phí:** prompt của v2 có thêm context nên **avg tokens gần gấp đôi** (57.0 → 109.1, +91%) và **tổng cost tăng 89%** (0.000403 → 0.000762 USD). Nhưng **cost / câu đúng giảm khoảng 11 lần** (0.00040275 → 0.00003627 USD). v1 rẻ mỗi câu nhưng gần như toàn trả lời sai, nên tiền bỏ ra gần như phí. Vì vậy "cost / câu đúng" là thước đo đúng để so 2 phiên bản, còn "cost / câu" thì đánh giá sai bản rẻ mà vô dụng. Bổ sung KB làm tokens tăng thêm 7% (116.7) nhưng cost / câu đúng còn giảm nhẹ (0.0000355).
- **Latency:** p50 trong eval tăng 167.8 → 209.3 ms (+41.5 ms) do thêm bước retrieval (khoảng 20 ms) và prompt dài hơn (mock cộng 0.2 ms / input token). p95 của eval (1522 ms ở cả v1 lẫn v2) **không dùng để so latency được**: 23 câu là mẫu quá nhỏ, chỉ 1–2 request chậm là p95 nhảy vọt. Ngoài ra câu đầu tiên của mỗi lần eval (`basic-01`) luôn mất khoảng 2.2 s vì trên Windows, `localhost` được thử qua IPv6 `::1` trước. Agent chỉ nghe IPv4 (`0.0.0.0`) nên kết nối `::1` bị từ chối sau **2.02 s** (đã đo riêng) rồi mới sang `127.0.0.1`. Latency phải đo bằng load test (có request khởi động không tính vào kết quả), như mục 2.
- **Kết luận:** v2 đáng giá. Chi phí và latency tăng vừa phải, đổi lại chất lượng tăng gấp nhiều lần.

### 3.1 Quality gate trong CI

File job: [`.github/workflows/quality-gate.yml`](.github/workflows/quality-gate.yml). Job chạy trên mỗi pull request đụng tới `benchmark-observability/`: cài requirements, chạy agent v2 (`MOCK_ERROR_RATE=0` để gate không fail ngẫu nhiên), chờ `/health`, rồi chạy

```bash
python eval/run_eval.py --url http://localhost:8000 --min-accuracy 0.85 \
  --baseline reports/eval_v2_fixed.json --out reports/eval_ci.json
```

**Cách exit code chặn merge:** `run_eval.py` kết thúc bằng `raise SystemExit(0 if gate_ok else 1)`, với `gate_ok = accuracy >= min_accuracy and not regressions`. Exit code ≠ 0 thì step fail, job `quality-gate` fail, PR hiện check đỏ. Khi bật branch protection *"Require status checks to pass before merging"* cho check này thì PR **không merge được**. Đã thử trên máy: eval v1 với `--min-accuracy 0.85` in `❌ FAIL` và **exit code 1**; eval v2 sau khi sửa in `✅ PASS` và **exit code 0**. `load_test.py` dùng cùng cơ chế (exit 1 khi vi phạm SLO) nên cũng gắn được vào CI làm performance gate.

### 3.2 Exercise 7.1 — đọc metrics

`curl -s http://localhost:8002/metrics | grep -v "^#"` sau 1 request `/ask`:

- **Counter** (chỉ tăng, dùng với `rate()`): `http_requests_total{method="POST",route="/ask",status="200"} 1.0`, `llm_tokens_total{model="mock-llm-small",type="input"} 91.0`, `llm_cost_usd_total{model="mock-llm-small"} 4.665e-05`
- **Gauge** (lên xuống): `agent_in_flight_requests 0.0`, `agent_info{model="mock-llm-small",version="v2"} 1.0`
- **Histogram** (bucket + `_sum` + `_count`, tính percentile bằng `histogram_quantile`): `http_request_duration_seconds_bucket{le="0.3",method="POST",route="/ask"} 1.0`, `..._count 1.0`, `..._sum 0.2459923000424169`

**Vì sao label `route` dùng `route.path` chứ không dùng `request.url.path`?** Mỗi tổ hợp giá trị label là **một time series riêng** trong Prometheus. Bot quét gọi hàng nghìn URL lạ (`/wp-admin`, `/.env`, `/a1b2c3`, ...). Nếu dùng URL thật, mỗi URL sinh thêm series mới cho `http_requests_total` và **12 series bucket** cho `http_request_duration_seconds`. Cardinality bùng nổ không giới hạn, Prometheus tốn RAM và query chậm, kẻ tấn công thậm chí có thể làm sập hệ thống giám sát. Dùng route template thì mọi URL không khớp gộp chung vào `unmatched`. Đã thử gọi 3 URL lạ, kết quả chỉ có **1** series: `http_requests_total{method="GET",route="unmatched",status="404"} 3.0`.

**Nhiệm vụ: thêm `agent_question_length_chars`** (đã thêm vào `develop/app.py`):

```python
QUESTION_LENGTH = Histogram(
    "agent_question_length_chars", "Độ dài câu hỏi gửi tới /ask (ký tự)",
    buckets=(20, 50, 100, 200, 500, 1000, 2000),
)
# trong ask(), trước khi gọi LLM:
QUESTION_LENGTH.observe(len(body.question))
```

Kết quả: `agent_question_length_chars_bucket{le="50.0"} 1.0`, `agent_question_length_chars_count 1.0`, `agent_question_length_chars_sum 40.0` (câu hỏi dài 40 ký tự). Dùng **Histogram** chứ không dùng label `length="40"`, vì độ dài có vô số giá trị, làm label sẽ bùng nổ cardinality như trên. Bucket cố định giới hạn số series (8 bucket) mà vẫn biết được phân bố. Bucket tối đa 2000 khớp với `max_length=2000` của `AskRequest`.

---

## 4. Observability stack (production)

**Cách chạy** (trong `06-lab-complete/benchmark-observability/production/`, Docker Desktop 28.0.4, VM 8 CPU / 3.9 GB):

```bash
docker compose -p k5-day14-observability up -d --build --scale agent=1     # rồi --scale agent=3 (mục 5)
docker run --rm -v "$PWD/prometheus:/p" --entrypoint promtool prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml
#   → Processing test file: /p/alert_rules_test.yml  SUCCESS
```

Ba vấn đề gặp phải khi chạy stack, mình đã sửa hoặc xử lý như sau:

1. **Lỗi sẵn có trong `production/Dockerfile`: agent crash `ModuleNotFoundError: No module named 'uvicorn'`.** Stage builder `pip install --user` cài vào `/root/.local` rồi copy sang `/home/agent/.local`. Nhưng user `agent` được tạo với `useradd -d /app` nên `HOME=/app`, và Python tìm user site-packages ở `/app/.local`. Cách sửa: thêm `ENV PYTHONUSERBASE=/home/agent/.local` (giống Dockerfile Part 5). Trước khi sửa, mọi request qua Nginx đều **502**.
2. **`docker compose run locust` tự scale lại agent.** Chạy Locust bằng `docker compose run` (lệnh trong CHECKPOINTS) thì Compose khởi động luôn các dependency (`locust → nginx → agent`) và đưa `agent` về `deploy.replicas: 2`. Lần chạy "1 instance" đầu tiên vì vậy thực ra có 2 instance (thấy `agent-4` được tạo đúng lúc Locust bắt đầu). Mình chạy lại với **`docker compose run --rm --no-deps locust ...`** và in danh sách container agent trước/sau mỗi lần chạy để chắc chắn đúng 1 / đúng 3 instance.
3. **Mức tải cho thí nghiệm 7.6: 15 user thay vì 30.** Từ số liệu thật: chi phí trung bình khoảng $3.71e-5/request (Prometheus `increase(llm_cost_usd_total)/increase(llm_requests_total)`), và mỗi Locust user tạo khoảng 0.589 req/s `/ask` (`scale_1_stats.csv`: 58.86 req/s / 100 user). Ở 30 user, chi phí nền khoảng **$2.36/giờ**, đã vượt ngưỡng `LLMCostBurnRateHigh` ($2/giờ) ngay cả khi chưa đổi gì, nên thí nghiệm C không còn ý nghĩa. Ở 15 user, chi phí nền khoảng $1.2/giờ (đo được 1.22–1.25), dưới ngưỡng.

Ngoài ra: Windows resolve `localhost` thành `::1` trước nên Grafana và Jaeger trả `ERR_CONNECTION_RESET` hoặc treo. Mọi URL kiểm tra đều dùng `127.0.0.1`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png), 15 user, 3 instance
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)
- [x] Một trace lỗi trong Jaeger (có event `exception`): [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`AgentHighErrorRate` + `AgentInstanceDown`, nhóm `agent-slo` báo `FIRING (2)`): [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png)

**Exercise 7.4 — đọc dashboard** (số liệu nền: 15 user, 3 instance, v2 + `mock-llm-small`, lấy từ Prometheus cùng lúc chụp ảnh):

1. **p95 và avg:** p95 = **290 ms**, avg = **217 ms** (p50 = 176 ms, p99 = **1735 ms**). p95 và avg khá gần nhau nhưng p99 gấp khoảng 8 lần avg. Khoảng 3% request bị mock LLM làm chậm thêm 1.5 s nằm trong 1% cuối nên chỉ p99 thấy được, average thì pha loãng chúng. Đây là tail latency, lý do SLO đặt theo percentile (giống mục 2.2).
2. **Nginx có chia đều không:** có. Panel "Requests/s theo instance" là 3 đường gần trùng nhau: **3.19 / 3.21 / 3.10 req/s** cho 3 instance (lệch < 4%). Nginx round-robin qua `server agent:8000 resolve`.
3. **Input tokens/s > output tokens/s** (**738.8** so với **392.8** token/s, khoảng 1.9 lần): mỗi request gửi vào LLM gồm system prompt, tài liệu retrieval (RAG) và câu hỏi, nên prompt khoảng 80 token, còn câu trả lời của mock chỉ khoảng 44 token (trace chậm ở 4.3: `input_tokens=83`, `output_tokens=44`). v2 càng đưa nhiều context thì input càng lớn. Giá output đắt hơn input 4 lần (0.0006 so với 0.00015 USD/1K) nên tổng chi phí vẫn do cả hai đóng góp.
4. **`curl http://127.0.0.1:8080/metrics`** → **`HTTP/1.1 404 Not Found`**. `nginx.conf` có `location = /metrics { return 404; }`. `/metrics` lộ thông tin nội bộ (route, lưu lượng, chi phí, version) nên không public ra ngoài. Prometheus vẫn scrape được vì nó gọi thẳng từng container `agent:8000/metrics` trong mạng nội bộ Docker (DNS service discovery), không đi qua Nginx.

### 4.2 Thí nghiệm "phá hệ thống"

Locust headless 15 user chạy suốt các thí nghiệm. Đổi cấu hình bằng `.env` rồi `docker compose -p k5-day14-observability up -d --scale agent=3`. Thời gian tính **từ lúc 3 agent mới đã healthy** (D: từ lúc `docker pause`) đến lúc API `/api/v1/alerts` của Prometheus báo `firing`, lấy mẫu mỗi 5 s. Sau mỗi thí nghiệm đều trả `.env` về mặc định và chờ alert hết.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | pending sau 25 s → **firing sau 80 s** (`for: 1m`) | **Error rate**: 0.91% → **22.7%**. "Requests/s theo status code" thấy đường `503` tăng vọt. p95 không đổi (0.290 → 0.291 s): lỗi không làm chậm. |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | pending sau 20 s → **firing sau 141 s** (`for: 2m`) | **Latency p50/p95/p99**: p95 0.29 → **1.92 s**, avg 0.22 → 0.65 s, p50 chỉ 0.18 → 0.22 s. Panel **LLM latency p95** cũng lên **1.92 s**, nên cái chậm nằm ở LLM. |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | pending sau 25 s → **firing sau 146 s** (`for: 2m`) | **Cost burn rate (USD/giờ)**: **$1.22 → $18.17/giờ** (×14.9, cửa sổ 1 m). **LLM latency p95**: 0.198 → 0.493 s (×2.5); p95 `/ask` 0.29 → 0.67 s. |
| D | `docker pause` 1 instance (`agent-3`) | `AgentInstanceDown` | pending sau 10 s → **firing sau 40 s** (`for: 30s`) | **Instances up**: 3 → **2**. "Requests/s theo instance": instance bị pause rơi dần về 0 (lúc lấy số còn 0.45 req/s vì `rate()` cửa sổ 1 m; 2 instance còn lại tăng lên 3.54 / 3.59 req/s). Nginx log **16 lần `504`**, tất cả từ upstream `172.18.0.3` (instance bị pause, chờ hết `proxy_read_timeout 10s`). Metric của agent không đếm các 504 này. |
| E | `AGENT_VERSION=v1` | **Không có** (sau 180 s, `/api/v1/alerts` rỗng) | — | **Tokens/s**: input 732.6 → **345.8**, output 391.2 → **186.8** (giảm khoảng 53%). Cost/giờ 1.24 → 0.59 USD. Bảng "Phiên bản đang chạy" đổi sang `v1`. p95 còn *tốt hơn* (0.293 → 0.198 s) vì bỏ bước retrieval. Panel "Retrieval hit ratio" (cửa sổ 5 m) còn hiện 100% trong mấy phút đầu vì cửa sổ vẫn chứa dữ liệu v2; query cửa sổ 1 m thì không còn sample retrieval nào. |

*Ghi chú D:* D chạy **trong lúc A vẫn đang firing** để chụp được 2 alert cùng lúc. Vì vậy panel Error rate lúc đó đỏ là do A, không phải do D. README dự đoán đúng: ở D, lỗi người dùng gặp là 504 từ Nginx, còn panel Error rate của agent không thấy. Muốn bắt loại lỗi này cần giám sát ở tầng load balancer (metric của Nginx) hoặc synthetic check từ bên ngoài. Thứ tự thời gian firing (D 40 s < A 80 s < B 141 s ≈ C 146 s) khớp với `for:` của từng rule (30 s / 1 m / 2 m / 2 m) cộng thời gian để `rate()` trên cửa sổ 1 m (hoặc 5 m) vượt ngưỡng.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

v1 trả lời sai gần hết (eval: 4.3% so với 100%) nhưng về hạ tầng thì "khoẻ" hơn: ít lỗi, nhanh hơn, rẻ hơn. Metric hạ tầng (4 Golden Signals) chỉ đo *nhanh / ổn định / tốn bao nhiêu*, không đo *đúng hay sai*. Cách phát hiện:

1. **Chặn trước khi deploy:** quality gate eval trong CI (`.github/workflows/quality-gate.yml`, mục 3.1). Bản v1 có accuracy 4.3% < 85% nên `run_eval.py` exit 1 và PR không merge được. Đây là lớp chính.
2. **Canary + eval online:** deploy cho một phần nhỏ traffic, chạy golden dataset (hoặc LLM-as-judge trên mẫu traffic thật) liên tục với bản mới, so với baseline trước khi tăng traffic.
3. **Alert trên "proxy metric" của chất lượng:** retrieval hit ratio < 50% trong 10 phút, tokens/request hay độ dài câu trả lời giảm đột ngột so với tuần trước, `agent_info{version}` đổi ngoài lịch release. Mỗi tín hiệu riêng lẻ chưa chắc là lỗi, nhưng đủ để gọi người kiểm tra.
4. **Tín hiệu từ người dùng:** tỉ lệ 👎 / báo câu trả lời sai, tỉ lệ hỏi lại cùng câu, tỉ lệ chuyển sang người hỗ trợ.

### 4.3 Debug bằng trace

Trace chậm nhất trong cửa sổ nền (Jaeger, lọc `duration ≥ 1s`, lấy trace dài nhất), ảnh: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png)

- `trace_id`: `55468e0d99f811068ac79e863f2b68f0`
- Tổng thời gian: **1736.7 ms** (`POST /ask` → 200)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1673.3 ms, 96.4%**). Còn lại: `agent.retrieve_context` 20.5 ms (1.2%); `agent.run` 1725.6 ms là span cha bao cả hai. Attributes của span LLM: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=83`, `gen_ai.usage.output_tokens=44`, `llm.cost_usd=3.885e-05`. **Kết luận: chậm do LLM upstream** (đây là 3% request "slow" của mock), không phải do code agent hay retrieval. Thêm instance không sửa được, chỉ timeout/retry, cache hoặc đổi model/provider mới giúp.
- Dòng log tương ứng (grep theo `trace_id` trong `docker compose -p k5-day14-observability logs agent`):

```json
{"ts": "2026-11-26T10:38:01.368105+00:00", "level": "INFO", "service": "observable-agent", "instance": "d6c8651392a7", "event": "llm_call", "trace_id": "55468e0d99f811068ac79e863f2b68f0", "span_id": "ca4ee3ffadf2a63b", "request_id": "973435770df6", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 83, "output_tokens": 44, "cost_usd": 3.9e-05, "llm_ms": 1671.9}
{"ts": "2026-11-26T10:38:01.369268+00:00", "level": "INFO", "service": "observable-agent", "instance": "d6c8651392a7", "event": "http_request", "trace_id": "55468e0d99f811068ac79e863f2b68f0", "span_id": "ca4ee3ffadf2a63b", "request_id": "973435770df6", "method": "POST", "route": "/ask", "status": 200, "ms": 1732.6}
```

`llm_ms=1671.9` trong log khớp với span LLM 1673.3 ms, và `ms=1732.6` khớp với span gốc 1736.7 ms. Log, trace và metric nối được với nhau qua `trace_id`.

**Trace lỗi** (lọc tag `error=true`), ảnh: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png). `trace_id` `2f98d943d9ad5f6ee35fc071aafbe453`, 109.6 ms, `POST /ask` → **503**. Span `chat mock-llm-small` (84.2 ms) có `status=ERROR`, `error.type=upstream_unavailable` và **event `exception`**: `exception.type=utils.mock_llm.LLMError`, `exception.message="Upstream LLM error: 503 Service Unavailable (mock)"`. Log cùng `trace_id`:

```json
{"ts": "2026-11-26T10:38:06.176858+00:00", "level": "INFO", "service": "observable-agent", "instance": "d6c8651392a7", "event": "llm_error", "trace_id": "2f98d943d9ad5f6ee35fc071aafbe453", "span_id": "d59899dc1de0c41c", "request_id": "28f2f9ef500d", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

`docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu là dòng `/ask` trong `benchmark-results/scale_<n>_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 58.86 | 200 | 560 | 1700 | 70 / 6981 (1.00%) |
| 3 | 60.44 | 190 | 230 | 1700 | 86 / 7178 (1.20%) |

**Giải thích kết quả:**

- **RPS gần như không đổi (58.9 so với 60.4)** vì Locust là mô hình *closed-loop*: 100 user, mỗi user nghỉ 0.5–2 s giữa 2 request. Lượng tải gửi vào bị giới hạn bởi số user (khoảng 100 / (1.25 s nghỉ + latency) ≈ 60 req/s), không phải bởi năng lực server. Cả 1 lẫn 3 instance đều phục vụ hết lượng tải đó, nên muốn thấy giới hạn RPS phải tăng số user.
- **p95 cải thiện rõ: 560 → 230 ms (−59%).** Với 1 instance, cả khoảng 60 req/s dồn vào **một process uvicorn (1 event loop) bị giới hạn `cpus: 0.5`**. Phần CPU của mỗi request (Pydantic, JSON log, Prometheus, tạo và export span OpenTelemetry) phải xếp hàng, và CFS quota 0.5 CPU có thể cắt CPU theo từng nhịp, nên một phần request chờ thêm vài trăm ms (Prometheus lúc chạy: p95 ≈ 0.40 s, in-flight ≈ 11–12). Với 3 instance, mỗi instance chỉ nhận khoảng 21 req/s (Prometheus: 20.96 / 20.91 / 20.80), hàng đợi gần như biến mất (p95 ≈ 0.29 s, in-flight ≈ 17 chia cho 3).
- **p99 không đổi (1700 ms) và p50 gần như không đổi (200 → 190 ms): nút thắt của tail latency nằm ở LLM upstream.** LLM latency p95 là 0.198 s ở cả hai lần, và khoảng 3% request bị mock LLM làm chậm thêm 1.5 s dù đi vào instance nào. Scale ngang chỉ xử lý được nghẽn **ở tầng agent** (hàng đợi, CPU). Còn độ trễ của nhà cung cấp LLM thì phải xử lý bằng timeout + retry/hedging, cache câu trả lời, streaming, hoặc model/provider nhanh hơn.
- Failures khoảng 1% ở cả hai lần là `MOCK_ERROR_RATE=0.01` (lỗi 503 upstream mô phỏng), không phải do quá tải.

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. **Percentile mới phản ánh trải nghiệm người dùng, average thì không.** Với `MOCK_SLOW_RATE` từ 0 lên 0.03, avg chỉ tăng 20% còn p99 tăng ×3.4. Ở dashboard, p95 290 ms trông ổn nhưng p99 1.7 s. SLO và alert phải đặt theo p95/p99. Muốn đo latency phải dùng load test đủ mẫu (eval 23 câu là quá ít).
  2. **Metric hạ tầng không đo được chất lượng, và metric của một service không thấy lỗi ở tầng trước nó.** Thí nghiệm E (v1 trả lời sai gần hết) không làm alert nào kêu, chỉ eval và quality gate bắt được. Thí nghiệm D: 16 lỗi 504 ở Nginx không hiện trong error rate của agent. Cần kết hợp eval trong CI, metric ở load balancer và synthetic check.
  3. **Ba trụ cột phải nối với nhau qua `trace_id`, và số liệu chỉ đáng tin khi đã kiểm tra lại setup.** Dashboard báo p99 cao → Jaeger chỉ ra span `chat mock-llm-small` chiếm 96.4% → grep log theo `trace_id` ra đúng request (`llm_ms=1671.9`). Mình cũng gặp 3 lần số liệu sai do setup chứ không do hệ thống: Dockerfile thiếu `PYTHONUSERBASE` (toàn 502), `compose run` tự scale lại agent (lần "1 instance" thực ra có 2), laptop ngủ giữa thí nghiệm. Luôn kiểm tra lại số instance, cấu hình và log trước khi tin một con số.
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?
  - **Chất lượng:** chạy eval online định kỳ (golden dataset + LLM-as-judge trên mẫu traffic) và alert khi accuracy giảm; alert khi `retrieval hit ratio` < 50% trong 10 phút; metric tỉ lệ 👎 / phản hồi xấu của người dùng.
  - **Tầng ngoài agent:** metric của Nginx/LB (tỉ lệ 502/504, upstream response time) và **synthetic check** `/ask` từ bên ngoài (bắt được loại lỗi ở D); alert error rate tính ở LB chứ không chỉ ở agent.
  - **LLM provider:** latency và tỉ lệ lỗi theo provider/model, đếm timeout/retry, alert riêng khi LLM p99 > 3 s; tỉ lệ request bị rate-limit (429) từ provider.
  - **Chi phí:** cost theo ngày/tháng so với budget (giống cost guard Part 6) với ngưỡng 50/80/100%; cost theo version để thấy ngay khi bản mới tốn hơn; tokens/request p95 (prompt phình to).
  - **Saturation:** CPU throttling (`container_cpu_cfs_throttled_*`) và bộ nhớ của container, độ dài hàng đợi / in-flight theo instance, kết hợp HPA để tự scale.
  - **Vận hành alert:** Alertmanager gửi Slack/PagerDuty, mỗi alert có runbook, và các SLO theo error budget (burn-rate nhiều cửa sổ 1 h/6 h) thay cho ngưỡng tĩnh.
