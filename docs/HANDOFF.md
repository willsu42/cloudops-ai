# 交接紀錄：Week 2（2A、2B 已完成，接下來是 2C）

> 給下次啟動的人（或 Claude）。2C–2H 照這份接著做。Week 2 全部完成後刪除本檔。
> 完整的分階段設計在 `/Users/willsu/.claude/plans/based-on-this-plan-wiggly-simon.md`。這份交接已經包含該檔的必要內容。

## 1. 方向與限制（沿用 Week 1）

- 參考系統是 OpenTelemetry Demo，聚焦 checkout 流程。Week 2 的目標是文件的 ingestion 加上基本 RAG，答案要附可解析的引用。
- **AWS 還沒開通。** Week 2 全程用 mock provider。Bedrock adapter 只寫程式碼、用 stub 測試，真實的 smoke test 等開通後再補。
- Mock embedding 只驗證流程，**不能**拿來宣稱檢索品質。
- 未經使用者明確要求，不 commit、不 push、不部署、不呼叫付費 API、不加 LICENSE。commit 之前先問。
- 每個階段一個 commit，push 後確認 CI 綠燈。

## 2. 本輪完成的內容

**本輪的變更都還沒 commit**，基準是 `0ac8649`。

### 2A 語料與案例擴充
- 從同一個 docs commit `145fd11` 新增 `service-quote.md`、`service-frontend.md`、`service-kafka.md`，原文未改動，manifest 共 12 份。
- `manifest.json` 的文件項目新增選填欄位 `retrieved_on`。這三份是 2026-11-23 取得的，其餘沿用 manifest 層級的 2026-11-17。`app/seed.py` 的 `ManifestDocument` 已支援這個欄位。
- `dev.jsonl` 從 7 個增加到 11 個：新增 `dev-quote-001`、`dev-kafka-001`、`dev-frontend-001`、`dev-insufficient-002`。
- 更新了 `datasets/corpus/README.md`、`datasets/evaluation/README.md`、根目錄 `README.md`。
- **Makefile 修正：** `migrate` 和 `seed` 改用 `docker compose run --rm --build`。原本會用到舊的 image，跑到舊程式碼。
- 驗證結果：`make seed` 跑兩次，都是 12 docs / 12 versions，retrieved_on 為 9 筆 11-17、3 筆 11-23。

### 2B 解析與切塊（純函式）
- `app/rag/parsing.py` 的 `parse_markdown(source) -> ParsedDocument(title, blocks)`：
  - Block 有三種 kind：`heading`、`text`、`code`，記錄 1-based 的 `start_line` / `end_line`。
  - 移除 front matter、Hugo shortcode、單行 HTML 註解。
  - fenced code（``` 或 ~~~，可縮排）保持原樣，裡面的 `#` 不會被當成標題。
- `app/rag/chunking.py` 的 `chunk_markdown(source, **limits) -> list[Chunk]`：
  - Chunk 欄位：`index`、`text`、`heading_path`、`start_line`、`end_line`、`token_estimate`、`content_sha256`、`chunker_version`。
  - `CHUNKER_VERSION = "md-heading-v1"`；預設 `max_tokens=500`、`overlap_tokens=75`、`max_code_tokens=1000`。
  - token 數是估計值，約 4 個字元算 1 token，不是模型的 tokenizer。
  - 規則：
    - 依 section 打包，chunk 不跨 section。
    - heading 和它後面的內容放在同一個 chunk。
    - 不超過 1000 token 的 code block 不切；更大的依行切開，每段重新補上 fence。
    - overlap 只取完整的 text block，不取 code，也不跨 section。
- 實際語料的切塊結果：12 份共 **105 chunk**，最大 807 token（architecture.md 的 mermaid 服務圖，整塊保留）。
- 刻意保留的部分：frontend、payment 的「TBD」小段（約 3 token）照原文保留，可作為「文件沒寫這部分」的 abstain 證據。
- 測試在 `tests/unit/test_chunking.py`，共 23 個，涵蓋：heading 路徑、行號回對原文、code 不被切開、超大 code 和段落的切分、overlap 不跨 section、可重現性、參數檢查、實際語料逐份檢查、架構圖為單一 chunk。

### 驗證結果（本機）
| 檢查 | 結果 |
| --- | --- |
| 後端測試（真實 pgvector test DB） | 40 passed |
| ruff format、ruff check、mypy strict | 全過 |
| 前端測試 | 5 passed |

GitHub CI 尚未執行，因為還沒 push。

## 3. 接下來的階段

依賴關係：2C → 2D → 2E → 2F；2G 在 2C/2E 之後任何時間都可以做；2H 最後。

### 2C Ingestion 管線與 schema
- **migration `0002`：** 建立 `document_chunks`。
  - 欄位：id（UUIDv5，由 `version_id` + `chunker_version` + `index` 算出）、`document_version_id` FK、`index`、`text`、`heading_path`（`text[]`）、`start_line`、`end_line`、`token_estimate`、`content_sha256`、`chunker_version`、`embedding_model`、`embedding_dim`、`embedding vector(1024)`。
  - **維度寫死在 migration 裡，不從設定讀取。**
  - 另建 `ingestion_runs`：記錄狀態、錯誤、時間、chunk 數。
- **`app/providers/embeddings.py`：**
  - `EmbeddingProvider` Protocol：`model_id`、`dim`、`embed(texts) -> list[list[float]]`。
  - `MockEmbeddingProvider`：用 hash 產生 1024 維、正規化過的向量。docstring 寫明只驗證流程。
- **`app/ingest.py` 與 `make ingest`：**
  - 讀 manifest，對每個 active document_version 呼叫 `chunk_markdown`，再做 embedding 並寫入。
  - 同一個 version 加同一組 `(chunker_version, embedding_model)` 已存在時跳過。
  - 新的一組寫入成功後才啟用，然後停用舊的；失敗時舊的保持 active。
  - 需要「active」的概念：在 chunk 或 version 上加 `is_active`，或建一張 active set 表，由 2C 決定。
- **重用：** `app/seed.py` 的 `load_manifest`、`version_id`、`ON CONFLICT` 寫法；`tests/conftest.py` 的 `migrated_database`、`db_engine`。
- **測試：** 重跑兩次 chunk 數和 ID 不變；故意讓 embedding 失敗時舊資料仍是 active。

### 2D 檢索與 source API
- `app/rag/retrieval.py`：用 `<=>` 做 exact cosine search，先套用 service 和 active 過濾再排序，取 top-k。空檢索門檻寫進設定。
- `GET /api/sources/{chunk_id}`：回傳 text、heading_path、行號、source_url、source_file_url、版本。不存在或未 active 時回 404。

### 2E 生成、引用與 abstain
- `app/providers/llm.py`：`LLMProvider` Protocol 和決定性的 mock。
- 答案 schema：summary、claims（每個 claim 帶 citation chunk id）、limitations、abstained。
- `app/rag/citations.py`：citation 必須在本次檢索結果中，不合格的剔除或標記。空檢索時不呼叫 LLM，直接 abstain。
- `POST /api/ask`：同步執行，問題長度有上限。

### 2F 提問與引用檢視 UI
- Ask 頁面、引用元件，點引用開出 source 段落。Markdown 要 sanitize，不允許 raw HTML。
- `vite.config.ts` 的 proxy 加 `/api`。
- 元件測試涵蓋：答案、abstain、錯誤、開啟引用。

### 2G Bedrock adapter（只寫程式）
- `app/providers/bedrock.py`：Titan V2 走 `invoke_model`，生成走 Converse；用 botocore Stubber 測試。需要新增 boto3 依賴。
- `PROVIDER_MODE=aws` **仍然拒絕**，等真實 smoke test 通過才開放。
- 新增 `docs/rag.md`：模型、維度、cosine 距離、重建索引程序，標明「待 AWS 驗證」。

### 2H 收尾
- 12 份文件和 ≥10 個問題用 mock 跑完整流程。
- 用 fixture 驗證不支援的格式（如 PDF）會回傳明確的 limitation。
- 更新 README 和 PLAN 第 7 節 Week 2 的勾選，真實 provider 相關的項目標為「待 AWS」。

## 4. 未定事項

- **AWS 帳號與 region：** 尚未開通。建議最晚 Week 3 前開通，並申請 Bedrock 模型存取權。
- **PDF：** 建議只驗證拒絕路徑，使用者尚未明確決定。
- **向量維度：** 計畫建議 1024（Titan V2 預設），使用者尚未明確確認。**在 2C 寫 migration 之前要先確認**，因為之後改維度需要新的 migration 並重新 embedding。

## 5. 環境提醒

- Node 24 在 `/usr/local/bin`，PATH 上排前面的是 Homebrew 的 Node 25，執行前端前先 `export PATH=/usr/local/bin:$PATH`。pnpm 沒有全域安裝，用 `make PNPM="npx --yes pnpm@12.17.0" ...`。
- 執行 uv 前先 `unset VIRTUAL_ENV`，避免 Anaconda 的干擾。
- Docker Desktop 可能沒在執行，用 `open -a Docker` 啟動。
- 測試資料庫用 `make test-db` 啟動，關閉用 `make test-db-down`。
