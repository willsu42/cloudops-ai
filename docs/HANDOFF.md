# 交接紀錄：Week 1 實作（暫停於 2026-11-17）

> 給下次啟動的人（或 Claude）。讀完這份就能接著做。完成 Week 1 後可刪除本檔。

## 1. 背景與方向（以此為準）

- 專案：CloudOps AI，一個以證據為依據的事故調查助理。最終技術棧是 React、FastAPI、PostgreSQL/pgvector，之後會加上 LangGraph、Bedrock、ECS。
- **方向已變更：** 參考系統改為 **OpenTelemetry Demo**，先聚焦 checkout 流程，之後調查 checkout 故障。`docs/PROJECT_PLAN.md` 原本的五個虛構服務設計已被取代，修訂說明在該檔頂部。
- **Week 1 範圍：** 只做本機應用基礎和文件語料。**不做** RAG 推論、agent、job/worker、provider mock、雲端部署。
- **工具鏈：** Python 3.12 + uv + Ruff + mypy + pytest；React + TS + Vite + pnpm + Vitest + Testing Library；Node 24。不用 Poetry，也不用 npm lockfile。
- **限制：** 未經明確要求，不 commit、不 push、不開 PR、不部署 AWS、不呼叫付費模型、不發佈。授權尚未決定，**不要**自動加 LICENSE。

## 2. 目前狀態

- **全部未 commit。** 所有變更都在工作目錄。`PROJECT_PLAN.md` 已用 `git mv` 移到 `docs/`。
- **可能仍在執行的容器：** `cloudops-ai-db-1`、`cloudops-ai-api-1`、`cloudops-ai-test-db-test-1`。用 `make down` 和 `make test-db-down` 停止，兩者都不會刪資料。只有 `make clean` 會刪除開發資料庫的 volume。

### 已完成且本機驗證過

| 區塊 | 檔案 | 驗證結果 |
| --- | --- | --- |
| 語料 | `datasets/corpus/otel-demo/*.md`（9 份，原文未改動）、`manifest.json`、`README.md` | 有測試確認 manifest 與檔案一致、語料不含故障注入內容 |
| 後端 | `apps/backend/app/`：`core/config.py`、`core/logging.py`、`api/health.py`、`db/`、`seed.py`、`evaluation/cases.py` | 17 個 pytest 全部通過；ruff 和 mypy（strict）無錯誤 |
| DB | `apps/backend/migrations/versions/0001_initial.py` | 建立 vector extension、`services`、`documents`、`document_versions` |
| 評估 | `datasets/evaluation/dev.jsonl`（7 個案例）、`README.md` | 有 group 不可跨 split 的驗證；`test.jsonl` 刻意尚未建立 |
| 本機環境 | `docker-compose.yml`、`docker-compose.test.yml`、`apps/backend/Dockerfile`、`Makefile`、`.env.example`、`.gitignore` | 見下方的 Compose 手動驗證 |
| 前端 | `apps/frontend/`（`src/services/api.ts`、`src/components/ReadinessStatus.tsx`、測試） | lint、typecheck、5 個測試、build 都通過（Node 24.13） |
| CI | `.github/workflows/ci.yml`、`.gitleaks.toml` | 本機 gitleaks 掃描無洩漏；**GitHub CI 尚未實際跑過** |
| 計畫 | `docs/PROJECT_PLAN.md` 頂部的修訂區塊 | — |

Compose 手動驗證的結果：

- `make up` 之後 `/health/ready` 回 200。
- `make seed` 跑兩次，筆數都是 10/9/9。
- 停掉 db 時，ready 在 0.16 秒內回 503，live 仍回 200；db 重啟後約 2 秒恢復。
- 經 Vite proxy 取得 readiness 正常；API 停掉時 proxy 回 502，UI 顯示「API unreachable」。
- api 容器以 uid 999（非 root）執行。

## 3. 剩下的工作（依序）

1. **改寫 `docs/PROJECT_PLAN.md` 第 7 節的 Week 1 條目**（約第 378–397 行），讓它反映修訂後的範圍：OTel 語料、無 provider mock、無 worker、評估改為真實標註案例。
2. **`README.md`（根目錄）：** 前置需求、設定、安裝、測試、疑難排解、目前限制，並註明授權尚未決定。內容要寫明：
   - pnpm 的安裝方式：優先 `corepack enable pnpm`，若 corepack 不可用或沒有寫入權限，改用 `npm install -g pnpm@12.17.0`。
   - Node 24（見 `apps/frontend/.nvmrc`）。
   - 從乾淨 checkout 開始的順序：`make setup` → `make up` → `make seed` → `make frontend`，測試用 `make test`。
   - DB 位址：容器內是 `db:5432`，主機上是 `127.0.0.1:5433`；測試資料庫在 `127.0.0.1:55432/cloudops_test`。
3. **`docs/architecture.md`：** 區分「已實作的本機架構」和「目標雲端架構」（後者可沿用 PROJECT_PLAN §3 的圖，標示為規劃）。
4. **`docs/adr/001-custom-rag.md`、`docs/adr/002-single-bounded-agent.md`：** 明確標示為未來能力（Status: Accepted (planned)）。
5. **從乾淨 checkout 驗證：** 用 `git stash -u` 或另開 worktree / clone，照 README 步驟逐字執行一遍，確認可行。
6. **最終報告：** 實作內容、啟動與測試指令、實際的檢查結果、阻礙與偏差、Week 2 待辦。

## 4. 已做的決定與偏差（不要重新討論）

- **文件身分：** `documents.source_key` 是邏輯文件；每個修訂版本是一筆 `document_versions`。content hash 只用來去重，不當身分。ID 是由穩定 key 算出的 UUIDv5。
- **沒有 vector 欄位。** chunk 和 embedding 在 Week 2 才加，維度要寫死在那個 migration 裡。
- **沒有 provider 介面或 mock**，因為目前沒有程式碼用到。`PROVIDER_MODE` 只接受 `mock`，設成 `aws` 會在啟動時報錯。
- **Settings 不讀 `.env` 檔**，環境變數由 Compose 提供；根目錄的 `.env` 只給 Compose 做變數替換。
- **測試隔離：**
  - DB 測試需要 `TEST_DATABASE_URL`，資料庫名稱必須以 `_test` 結尾，否則測試會拒絕執行。
  - 沒設定 `TEST_DATABASE_URL` 時，DB 測試會 skip；設了 `REQUIRE_DB_TESTS=1`（CI 和 `make test-backend` 有設）則改為失敗。
  - 測試開始時會清空 test DB 的 schema 再跑 migration。
- **故障模擬測試：** `tests/support/tcp.py` 裡的 `ToggleProxy` 用來模擬 DB 斷線再恢復，`SilentServer` 模擬 DB 卡住不回應。
- **前端：**
  - 改用 TypeScript `~6.0.3`，因為 typescript-eslint 8.72 只支援 TypeScript < 6.1，不支援 7。
  - `@types/node` 用 `~24`。
  - `pnpm-workspace.yaml` 設了 `minimumReleaseAge: 20160`，只安裝發佈超過 14 天的版本。
- **本機有兩個 Node：** PATH 上排前面的是 Homebrew 的 Node 25，Node 24 在 `/usr/local/bin`。執行前端指令時用 `export PATH=/usr/local/bin:$PATH`。pnpm 沒有全域安裝，之前用的是 `npx --yes pnpm@12.17.0 <cmd>`；Makefile 可用 `make PNPM="npx --yes pnpm@12.17.0" ...` 覆寫。
- **後端版本：** `pyproject.toml` 設了 `exclude-newer = 2026-11-03`。在本 shell 執行 uv 前先 `unset VIRTUAL_ENV`，否則 Anaconda 的 venv 會干擾。
- **語料：**
  - 刻意排除 Feature Flags 頁（列有故障注入 flag）和 Kubernetes 頁。
  - quote 服務的頁面尚未收錄。
  - 文件版本和應用版本分開記錄：
    - 文件：`opentelemetry.io@145fd11`
    - 應用 main：`opentelemetry-demo@b825c7c`
    - 最新 release：3.1.0
- **CI：** actions 都釘在 commit SHA：
  - checkout v7.0.1
  - setup-uv v10.3.3
  - setup-node v7.1.0
  - pnpm/action-setup v6.1.1（peeled commit）
  - gitleaks 用 docker image `v8.30.1`，不用 gitleaks-action，避免授權金鑰需求。

  建議人工審閱這些 SHA。

## 5. Week 2 預告

文件 ingestion（Markdown 解析、heading 切塊、位置資訊），Bedrock Converse 與 Titan V2 adapter（先查清楚 region 和維度），`document_chunks` 加上固定維度的 vector 欄位，exact vector search，引用驗證，空檢索的處理。開始調整檢索或 prompt 之前，要先設計並凍結 held-out `test.jsonl`。
