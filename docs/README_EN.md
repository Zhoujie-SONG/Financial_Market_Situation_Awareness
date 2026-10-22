# Financial Market Situation Awareness

A research workspace for mainland China, Hong Kong and US markets. Existing single-stock dashboards, industry reports, market reviews, Web/Bot interfaces, scheduling and notifications now support optional read-only ETF data from [personal-quant](https://github.com/Zhoujie-SONG/personal-quant).

[简体中文](../README.md) · [繁體中文](README_CHT.md) · [Full guide](full-guide_EN.md) · [PIT integration contract](personal-quant-integration.md)

## Capabilities

| Workflow | Entry point |
|---|---|
| Single-stock research | `python main.py --stocks 600519,hk00700,AAPL --no-notify` |
| Industry macro → constituents → CIO report | `python main.py --mode industry --industry cpo --format all --no-notify` |
| Market review | `python main.py --market-review` |
| Web workspace | `python main.py --webui-only` |
| Scheduler and existing notification channels | `python main.py --schedule` |
| Canonical ETF daily bars / field-level metadata | Configure `PERSONAL_QUANT_CANONICAL_DIR` and optional metadata policy |
| Explicit historical PIT dataset export | `python -m src.services.personal_quant --help` |

Existing Agent, backtest and portfolio tools remain available. PIT daily bars do not make current news, LLM knowledge or the existing backtester historically PIT-safe.

## Quick start

Python 3.10+ for the original application; Python 3.11+ for the optional bridge. PowerShell:

```powershell
git clone https://github.com/Zhoujie-SONG/Financial_Market_Situation_Awareness.git
cd Financial_Market_Situation_Awareness
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -X utf8 -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `STOCK_LIST` and a supported model/provider in the local `.env`. See [LLM configuration](LLM_CONFIG_GUIDE_EN.md). Never commit credentials.

```powershell
python main.py --stocks 600519 --dry-run --no-notify
python main.py --stocks 600519,hk00700,AAPL --no-notify
```

Build `apps/dsa-web/` with `npm ci` and `npm run build` before starting Web UI when frontend artifacts are absent. Deployment and desktop entry points are documented in [DEPLOY](DEPLOY_EN.md) and [desktop packaging](desktop-package.md).

## personal-quant integration

Install `requirements-personal-quant.txt`, then set:

```dotenv
PERSONAL_QUANT_CANONICAL_DIR=/path/to/personal-quant/data/canonical
PERSONAL_QUANT_METADATA_POLICY=/path/to/personal-quant/configs/metadata_resolution.yaml
PERSONAL_QUANT_ALLOW_FALLBACK=false
```

The bridge reads existing finalized unadjusted Longbridge observations. It does not ingest or modify source data. Current ETF analysis uses SYSTEM_REPLAY; failure stops the affected analysis unless legacy fallback is explicitly enabled. Bound canonical snapshots bypass the mutable application daily cache.

Historical queries require explicit mode/as-of; Economic queries additionally require research cutoff. Output includes revision IDs, value hashes, availability policy, historical semantics, metadata field statuses and a dataset snapshot fingerprint. Historical latest is distinct from true historical vintage.

Analysis of the current view stops when the latest local bar lags the latest closed session by more than `PERSONAL_QUANT_MAX_STALE_SESSIONS` (default 1). Canonical bars are unadjusted, unlike the forward-adjusted legacy providers; see "复权口径" in the integration contract.

See the [integration contract](personal-quant-integration.md) for commands, time semantics, Docker mounts, audit provenance, limitations and rollback.

## Disclaimer

This project is for research and learning. Scores, trend calls and entry/exit levels in reports are produced by rules and large language models and may rely on stale, incomplete or incorrect data. Nothing here is investment advice; you are responsible for any trading decision and its consequences.

## Validation and source

```bash
python -X utf8 -m pip install -r requirements-ci.txt
python -m pytest
```

Default collection is limited to `tests/` and excludes `network`. Run external-service tests explicitly with `python -m pytest -m network`. Install the optional bridge dependency to run its full Parquet/SQLite fixtures. Linux backend gate: `./scripts/ci_gate.sh`.

Built on [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis); the original [MIT License](../LICENSE) and copyright are retained. See [CHANGELOG](CHANGELOG.md).
