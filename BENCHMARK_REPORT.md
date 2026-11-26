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

_(CP8 — làm sau giờ lab)_

### 4.1 Screenshot

- [ ] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals)
- [ ] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian)
- [ ] Một trace lỗi trong Jaeger (có event `exception`)
- [ ] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | | | |
| B | `MOCK_SLOW_RATE=0.3` | | | |
| C | `LLM_MODEL=mock-llm-large` | | | |
| D | `docker pause` 1 instance | | | |
| E | `AGENT_VERSION=v1` | | | |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

...

### 4.3 Debug bằng trace

Chọn 1 trace chậm nhất bạn tìm được:

- `trace_id`: ...
- Tổng thời gian: ... ms
- Span chiếm nhiều thời gian nhất: ... (... ms, ...%)
- Dòng log tương ứng (grep theo `trace_id`):

```json

```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | | | | | |
| 3 | | | | | |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?

...

---

## 6. Kết luận

- 3 điều bạn học được về benchmark & observability:
  1. ...
  2. ...
  3. ...
- Nếu đưa agent này lên production thật, bạn sẽ thêm metric / alert nào nữa?

...
