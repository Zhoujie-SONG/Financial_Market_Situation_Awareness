# 金融市场态势感知系统

覆盖 A 股、港股、美股的研究工作台：把行情、技术指标、新闻和行业信息组织为单股报告与行业全景研报，再通过 Web、CLI、Bot 或通知渠道使用。ETF 研究可接入 [personal-quant](https://github.com/Zhoujie-SONG/personal-quant) 的本地 PIT 数据地基。

[English](docs/README_EN.md) · [繁體中文](docs/README_CHT.md) · [配置与部署](docs/full-guide.md) · [PIT 数据接入](docs/personal-quant-integration.md) · [更新日志](docs/CHANGELOG.md)

## 能力与入口

| 能力 | 运行入口 | 输出 / 用途 |
|---|---|---|
| 单股研究 | `python main.py --stocks 600519,hk00700,AAPL` | 技术面、新闻与风险信息组成的决策仪表盘 |
| 行业研究 | `python main.py --mode industry --industry cpo` | 行业宏观分析 → 成分股研究 → CIO 聚合，支持 JSON / HTML |
| 市场复盘 | `python main.py --market-review` | 指数、市场涨跌、板块表现 |
| Web 工作台 | `python main.py --webui-only` | 自选股、任务进度、历史报告、设置和 Agent 对话 |
| 定时 / 通知 | `python main.py --schedule` | 复用现有调度和企业微信、飞书、Telegram、Discord、Slack、邮件等渠道 |
| ETF canonical 数据 | 配置 `PERSONAL_QUANT_CANONICAL_DIR` | 已入库日线、immutable revision、来源与三时间戳；查询当前 SYSTEM_REPLAY 视图 |
| 历史 PIT 数据导出 | `python -m src.services.personal_quant --help` | 显式经济 / 系统时点、研究 cutoff、快照指纹、逐字段元数据解析结果 |

已有 Agent、回测和持仓页面继续属于本项目原有研究功能。PIT 日线接入仅约束该数据输入；新闻、实时行情、LLM 知识及既有回测模块仍各自有时间边界，不能据此宣称整个研究流程已通过历史 PIT / OOS 验证。

## 快速开始

基础运行需要 Python 3.10+；personal-quant 接入需要 Python 3.11+。以下为 PowerShell 命令：

```powershell
git clone https://github.com/Zhoujie-SONG/Financial_Market_Situation_Awareness.git
cd Financial_Market_Situation_Awareness
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -X utf8 -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填写自选股和一个可用的模型配置；模型名使用服务商实际支持的名称：

```dotenv
STOCK_LIST=600519,hk00700,AAPL
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://your-provider.example/v1
OPENAI_MODEL=your_model_id
```

其他模型渠道、Ollama、搜索服务和通知渠道见 [LLM 配置](docs/LLM_CONFIG_GUIDE.md) 与 [完整指南](docs/full-guide.md)。`.env` 只保存在本地，示例文件不含真实凭证。

```powershell
# 仅抓取数据，跳过模型分析与通知
python main.py --stocks 600519 --dry-run --no-notify
# 单股 / 行业研究
python main.py --stocks 600519,hk00700,AAPL --no-notify
python main.py --mode industry --industry cpo --format all --no-notify
# Web 服务
python main.py --webui-only
```

Web 源码位于 `apps/dsa-web/`。本地开发或缺少前端构建产物时：

```powershell
cd apps/dsa-web
npm ci
npm run build
cd ../..
python main.py --webui-only
```

服务地址跟随启动日志和配置。Docker、桌面端与 GitHub Actions 配置见 [部署指南](docs/DEPLOY.md)、[桌面打包](docs/desktop-package.md) 和 `.github/workflows/`。

## 接入 personal-quant

先在 personal-quant 完成行情 ingestion；本项目读取其 canonical 数据目录，不触发下载、不修改该目录，也不审批任何 vehicle candidate。

```powershell
python -X utf8 -m pip install -r requirements-personal-quant.txt
$env:PERSONAL_QUANT_CANONICAL_DIR = '<personal-quant目录>/data/canonical'
$env:PERSONAL_QUANT_METADATA_POLICY = '<personal-quant目录>/configs/metadata_resolution.yaml'
python main.py --stocks 510300,159915 --no-notify
```

配置后，内地 ETF 日线优先采用 canonical 数据；未配置时沿用原有多数据源流程。默认 canonical 查询失败即停止该 ETF 分析。只有显式设置 `PERSONAL_QUANT_ALLOW_FALLBACK=true` 才启用旧的在线日线来源，日志和 source 字段会保留真实来源。

canonical 结果保留 source、availability policy、historical semantics、research cutoff 与 `dataset_snapshot_id`。ETF 元数据复用 personal-quant 的 field resolver，保留 `RESOLVED / UNKNOWN / STALE / CONFLICT`，不会将未知字段补成“已验证”。

历史数据查询使用独立命令，避免混入当前新闻与实时行情：

```powershell
python -m src.services.personal_quant --symbol 510300.SH --start-date 2019-01-01 --end-date 2019-12-31 --as-of 2019-12-31T16:00:00+08:00 --mode economic --research-data-cutoff 2026-10-22T00:00:00+08:00 --output data/pit/510300_2019.json
```

Economic PIT 不等于真实 historical vintage。详细时间语义、缓存隔离、部署方式和排障见 [PIT 接入文档](docs/personal-quant-integration.md)。

## 配置与代码结构

| 路径 | 职责 |
|---|---|
| `main.py` / `server.py` | CLI / FastAPI 服务 |
| `pipelines/` | 单股与行业流程编排 |
| `src/core/`、`src/services/` | 分析业务、历史加载、PIT bridge |
| `data_provider/` | 原有行情源与 PersonalQuantFetcher |
| `.env` 的 `STOCK_LIST`、`config/industries.yaml` | 自选股与行业定义 |
| `api/`、`bot/` | API 与机器人接入 |
| `apps/dsa-web/`、`apps/dsa-desktop/` | React 工作台与桌面壳 |
| `templates/`、`src/reports/` | 报告渲染 |
| `tests/`、`scripts/`、`docs/` | 测试、运维入口与专题文档 |

## 验证

```powershell
python -X utf8 -m pip install -r requirements-ci.txt
python -m pytest
# 可选桥接的完整 fixtures 需要安装 requirements-personal-quant.txt
python -m pytest tests/test_personal_quant_bridge.py tests/test_industry_pipeline_contract.py
```

默认仅收集 `tests/` 并排除 `network` 测试；真实服务测试显式运行 `python -m pytest -m network`。Linux CI 的完整后端检查入口为 `./scripts/ci_gate.sh`。

## 开源来源

本项目沿用并扩展 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) 的基础能力，保留原 [MIT License](LICENSE) 与版权声明。新增的数据桥接依赖 personal-quant 的公开版本；两套系统的用途、数据状态和验证结果分别记录。


