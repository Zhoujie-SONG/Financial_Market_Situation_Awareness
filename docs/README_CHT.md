# 金融市場態勢感知系統

涵蓋 A 股、港股與美股的研究工作台，保留單股儀表盤、行業研報、市場復盤、Web/Bot、通知和排程，並可接入 [personal-quant](https://github.com/Zhoujie-SONG/personal-quant) 的本地 PIT 行情與逐欄位 ETF 中繼資料。

[简体中文](../README.md) · [English](README_EN.md) · [完整指南](full-guide.md) · [PIT 接入說明](personal-quant-integration.md)

## 功能入口

| 工作流程 | 指令 |
|---|---|
| 單股研究 | `python main.py --stocks 600519,hk00700,AAPL --no-notify` |
| 行業宏觀 → 成分股 → CIO 研報 | `python main.py --mode industry --industry cpo --format all --no-notify` |
| 市場復盤 | `python main.py --market-review` |
| Web 工作台 | `python main.py --webui-only` |
| 排程與既有通知渠道 | `python main.py --schedule` |
| ETF canonical 日線 | 設定 `PERSONAL_QUANT_CANONICAL_DIR` |
| 歷史 PIT 資料匯出 | `python -m src.services.personal_quant --help` |

既有 Agent、回測和持倉功能繼續保留。PIT 日線接入不代表當前新聞、模型知識或既有回測已通過歷史 PIT / OOS 驗證。

## 快速開始

基礎功能需要 Python 3.10+，personal-quant 接入需要 Python 3.11+。PowerShell：

```powershell
git clone https://github.com/Zhoujie-SONG/Financial_Market_Situation_Awareness.git
cd Financial_Market_Situation_Awareness
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -X utf8 -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在本地 `.env` 設定 `STOCK_LIST` 及可用模型渠道；模型、搜尋、通知設定見 [LLM 配置](LLM_CONFIG_GUIDE.md) 和 [完整指南](full-guide.md)。不要提交真實憑證。

```powershell
python main.py --stocks 600519 --dry-run --no-notify
python main.py --stocks 600519,hk00700,AAPL --no-notify
```

缺少前端產物時，先在 `apps/dsa-web/` 執行 `npm ci`、`npm run build`，再啟動 Web。Docker、桌面與 GitHub Actions 請參考 [部署](DEPLOY.md)、[桌面打包](desktop-package.md) 及 workflows。

## personal-quant 接入

安裝 `requirements-personal-quant.txt`，再設定：

```dotenv
PERSONAL_QUANT_CANONICAL_DIR=/path/to/personal-quant/data/canonical
PERSONAL_QUANT_METADATA_POLICY=/path/to/personal-quant/configs/metadata_resolution.yaml
PERSONAL_QUANT_ALLOW_FALLBACK=false
```

先在 personal-quant 完成 ingestion。本專案只讀取已入庫的 finalized、未復權 Longbridge observations，保留修訂、三時間戳、欄位狀態和快照指紋。當前 ETF 分析使用 SYSTEM_REPLAY，失敗預設停止；明確啟用 fallback 後才使用舊線上來源。canonical 日線不經過應用的可覆寫日線快取。

目前視圖下，本地最新 K 線落後最近已收盤交易日超過 `PERSONAL_QUANT_MAX_STALE_SESSIONS`（預設 1）個交易日時停止分析。canonical 日線為不復權價格，與舊鏈路的前復權口徑不同，詳見接入說明的「复权口径」。

歷史匯出明確指定 mode、as-of，Economic 模式還需 research cutoff。Historical latest 不等於真實 historical vintage。完整指令、時間語義、部署掛載與回退方式見 [接入說明](personal-quant-integration.md)。

## 免責聲明

本專案用於研究與學習。報告中的評分、趨勢判斷與買賣點位由規則與大型語言模型產生，可能基於過期、不完整或錯誤的資料，不構成任何投資建議。據此進行的交易決策及其後果由使用者自行承擔。

## 驗證與來源

```bash
python -X utf8 -m pip install -r requirements-ci.txt
python -m pytest
```

預設只收集 `tests/` 並排除 network；外部服務測試明確執行 `python -m pytest -m network`。橋接完整 fixtures 需安裝選用依賴。Linux 後端檢查入口為 `./scripts/ci_gate.sh`。

本專案沿用並擴展 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)，保留原 [MIT License](../LICENSE) 與版權聲明。[更新日誌](CHANGELOG.md)。
