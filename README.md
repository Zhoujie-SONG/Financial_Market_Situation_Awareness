<div align="center">

# 📈 股票智能分析系统

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

> 🤖 基于 AI 大模型的**双轨制**投研系统 — 支持「单股决策仪表盘」与「行业全景研报」两种模式，覆盖 A 股 / 港股 / 美股，每日自动分析并推送至企业微信 / 飞书 / Telegram / Discord / Slack / 邮箱

[**双轨制架构**](#-双轨制架构) · [**功能特性**](#-功能特性) · [**行业分析**](#-行业深度分析) · [**快速开始**](#-快速开始) · [**完整指南**](docs/full-guide.md) · [**常见问题**](docs/FAQ.md) · [**更新日志**](docs/CHANGELOG.md)

</div>

---

## 🏗️ 双轨制架构

本系统采用 **Pipeline 流水线架构**，通过统一 CLI 入口 + 模式路由实现两种分析范式：

| 模式 | CLI | 方法 |
|------|-----|------|
| **模式 A — 单股分析** | `python main.py` | 对自选股逐只进行量价 + 新闻的 LLM 深度分析，输出决策仪表盘 |
| **模式 B — 行业分析** | `python main.py --mode industry --industry cpo` | Top-Down 行业全景研报：宏观景气度 → 成分股下钻 → CIO 聚合 |

```
main.py 路由
  ├── --mode stock (默认)   →  SingleStockPipeline
  │     └── 保留并优化原有单股分析全流程
  │
  └── --mode industry       →  IndustryPipeline
        ├── Step 1: 行业宏观分析（新闻搜索 + LLM）
        ├── Step 2: 成分股并发下钻（循环调用 SingleStockPipeline）
        └── Step 3: CIO Agent 聚合（产业链点评 + 横向对比 + 配置建议）
```

**核心配置文件**:

| 文件 | 用途 |
|------|------|
| [`config/stocks.yaml`](config/stocks.yaml) | 单股模式 — 自选股列表（A 股 / 港股 / 美股） |
| [`config/industries.yaml`](config/industries.yaml) | 行业模式 — 行业定义 + 成分股（含产业链角色） |

> 更多细节见 [`pipelines/`](pipelines/) 目录。

---

## ✨ 功能特性

| 模块 | 功能 | 说明 |
|------|------|------|
| AI 决策 | 决策仪表盘 | 一句话核心结论 + 评分 + 买卖点位 + 风险警报 + 操作检查清单 |
| 行业分析 | 行业深度分析 | Top-Down 全景研报：宏观景气度 → 成分股对比 → CIO 配置建议 |
| 多维分析 | 技术面 / 行情 / 筹码 / 舆情 / 公告 / 资金流 / 基本面聚合 |
| 全球市场 | 多市场支持 | A 股、港股、美股、美股指数及常见 ETF |
| 策略系统 | 内置策略 | A 股复盘 / 美股 Regime / 均线 / 缠论 / 波浪 / 情绪周期等 |
| 大盘复盘 | 每日市场概览 | 指数表现、涨跌统计与板块强弱（支持 cn / hk / us / both） |
| Web 工作台 | 双主题管理 | 手动分析、配置管理、任务进度、历史报告、回测、持仓管理 |
| 智能导入 | 多格式支持 | 图片、CSV / Excel、剪贴板导入；代码 / 名称 / 拼音 / 别名补全 |
| 报告管理 | 历史追溯 | 历史报告查看、完整 Markdown 导出、重新分析与批量管理 |
| AI 回测 | 事后验证 | 对历史分析进行验证，查看方向准确率和模拟收益 |
| Agent 问股 | 策略对话 | 多轮策略问答，支持 11 种内置策略，Web / Bot / API 全链路 |
| 多渠道推送 | 通知分发 | 企业微信、飞书、Telegram、Discord、Slack、邮件等 |
| 自动化 | 定时运行 | 支持 GitHub Actions、Docker、本地定时任务和 FastAPI 服务模式 |

> 功能细节、数据源优先级、交易纪律等详见 [完整配置与部署指南](docs/full-guide.md)。

### 技术栈与数据来源

| 类型 | 支持 |
|------|------|
| AI 模型 | OpenAI 兼容、Gemini、DeepSeek、通义千问、Claude、Ollama 本地模型等 |
| 行情数据 | TickFlow、AkShare、Tushare、Pytdx、Baostock、YFinance、Longbridge |
| 新闻搜索 | Tavily、Bocha、Brave、MiniMax、SearXNG 等 |
| 社交舆情 | Stock Sentiment API（Reddit / X / Polymarket，仅美股，可选） |

> 完整规则见 [数据源配置](docs/full-guide.md#数据源配置)。

---

## 🏭 行业深度分析

行业模式是本系统的核心差异化能力，采用 **Top-Down（自上而下）** 分析范式：

```
输入: --industry cpo
  │
  ├── Step 1: 行业宏观分析
  │   ├── 搜索行业新闻（关键词匹配 + SearchService 多引擎）
  │   └── LLM 输出: 景气度 / 核心驱动 / 政策影响 / 周期位置
  │
  ├── Step 2: 成分股微观下钻（并发）
  │   ├── 中际旭创 → SingleStockPipeline.analyze_single()
  │   ├── 新易盛   → SingleStockPipeline.analyze_single()
  │   ├── 天孚通信 → SingleStockPipeline.analyze_single()
  │   └── ... (asyncio.Semaphore 控制并发)
  │
  └── Step 3: CIO Agent 聚合
      ├── 输入: 行业宏观 + N 只成分股精简摘要
      ├── LLM 输出: 产业链点评 / 龙头分析 / 横向对比 / 配置建议
      └── Token 管理: 单股 ≤300 字，最多 10 只详细分析
```

**预置行业**（可扩展，编辑 `config/industries.yaml`）:

| 行业 ID | 名称 | 龙头 |
|---------|------|------|
| `cpo` | CPO 光模块 | 中际旭创 |
| `semiconductor_memory` | 半导体存储 | 紫光国微 |
| `ai_infrastructure` | AI 算力基建 | 中际旭创 / 海光信息 |
| `new_energy_vehicle` | 新能源汽车 | 比亚迪 / 宁德时代 |

```bash
# 分析全部行业
python main.py --mode industry

# 分析单个行业
python main.py --mode industry --industry cpo
python main.py --mode industry --industry semiconductor_memory

# 输出格式控制（默认 json+html）
python main.py --mode industry --format html       # 仅生成精美 HTML 报告
python main.py --mode industry --format json       # 仅生成 JSON（下游决策用）
python main.py --mode industry --format all        # JSON + HTML

# 不推送通知
python main.py --mode industry --no-notify
```

---

## 🚀 快速开始

### 方式一：GitHub Actions（推荐）

> 5 分钟完成部署，零成本，无需服务器。

#### 1. Fork 本仓库

点击右上角 `Fork` 按钮将项目复制到你的仓库。

#### 2. 配置 Secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

**AI 模型配置（至少配置一个）**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `OPENAI_API_KEY` | OpenAI 兼容 API Key（支持 DeepSeek、通义千问等） | 可选 |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | 使用 OpenAI 兼容服务时填写 | 可选 |
| `GEMINI_API_KEY` | Google Gemini API Key | 可选 |
| `ANTHROPIC_API_KEY` | Anthropic Claude API Key | 可选 |

> Ollama 更适合本地 / Docker 部署，GitHub Actions 推荐使用云端 API。更多模型配置详见 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)。

**通知渠道配置（至少配置一个）**

| Secret 名称 | 说明 |
|------------|------|
| `WECHAT_WEBHOOK_URL` | 企业微信机器人 |
| `FEISHU_WEBHOOK_URL` | 飞书机器人 |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Telegram |
| `DISCORD_WEBHOOK_URL` | Discord Webhook |
| `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` | Slack Bot |
| `EMAIL_SENDER` + `EMAIL_PASSWORD` | 邮件推送 |

> 更多渠道、签名校验、分组邮件等配置见 [通知渠道详细配置](docs/full-guide.md#通知渠道详细配置)。

**自选股配置（必填）**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL,TSLA` | ✅ |

**新闻源配置（推荐）**

| Secret 名称 | 说明 | 推荐 |
|------------|------|:----:|
| `SERPAPI_API_KEYS` | SerpAPI — 搜索引擎结果，适合实时金融新闻 | 推荐 |
| `TAVILY_API_KEYS` | Tavily — 通用新闻搜索 API | 可选 |
| `BOCHA_API_KEYS` | 博查搜索 — 中文搜索优化 | 可选 |
| `BRAVE_API_KEYS` | Brave Search — 隐私优先，美股资讯补强 | 可选 |
| `MINIMAX_API_KEYS` | MiniMax — 结构化搜索结果 | 可选 |
| `SEARXNG_BASE_URLS` | SearXNG 自建实例 — 无配额兜底 | 可选 |

> 更多搜索源和降级规则见 [搜索服务配置](docs/full-guide.md#搜索服务配置)。

#### 3. 启用 Actions

`Actions` 标签 → `I understand my workflows, go ahead and enable them`

#### 4. 手动测试

`Actions` → `每日股票分析` → `Run workflow` → `Run workflow`

#### 完成

默认每个 **工作日 18:00（北京时间）** 自动执行，也可手动触发。默认非交易日（含 A / H / US 节假日）不执行。更多规则见 [完整指南](docs/full-guide.md#定时任务配置)。

### 方式二：本地运行 / Docker 部署

```bash
# 克隆项目
git clone <your-repo-url> && cd daily_stock_analysis

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env && vim .env

# 运行分析
python main.py
```

常用命令：

```bash
# ── 单股分析模式（默认）──
python main.py                                    # 分析自选股列表
python main.py --debug                            # 调试模式
python main.py --dry-run                          # 仅获取数据
python main.py --stocks 600519,hk00700,AAPL       # 指定股票

# ── 行业深度分析模式 ──
python main.py --mode industry                    # 分析全部行业（JSON+HTML）
python main.py --mode industry --industry cpo     # 分析指定行业
python main.py --mode industry --format json      # 仅输出 JSON
python main.py --mode industry --format html      # 仅输出 HTML 报告
python main.py --mode industry --format all       # JSON + HTML（默认）
python main.py --mode industry --no-notify        # 不推送

# ── 其他模式 ──
python main.py --market-review                    # 大盘复盘
python main.py --schedule                         # 定时任务
python main.py --serve-only                       # FastAPI 服务
python main.py --backtest                         # 回测
```

> Docker 部署、定时任务、云服务器部署等请参考 [完整指南](docs/full-guide.md)；桌面客户端打包请参考 [桌面端打包说明](docs/desktop-package.md)。

---

## 📱 推送效果

### 决策仪表盘

```
🎯 2026-02-08 决策仪表盘
共分析3只股票 | 🟢买入:0 🟡观望:2 🔴卖出:1

📊 分析结果摘要
⚪ 中钨高新(000657): 观望 | 评分 65 | 看多
⚪ 永鼎股份(600105): 观望 | 评分 48 | 震荡
🟡 新莱应材(300260): 卖出 | 评分 35 | 看空

⚪ 中钨高新 (000657)
📰 重要信息速览
💭 舆情情绪: 市场关注其AI属性与业绩高增长，情绪偏积极，但需消化短期获利盘和主力流出压力。
📊 业绩预期: 基于舆情信息，公司2025年前三季度业绩同比大幅增长，基本面强劲，为股价提供支撑。

🚨 风险警报:
风险点1：2月5日主力资金大幅净卖出3.63亿元，需警惕短期抛压。
风险点2：筹码集中度高达35.15%，表明筹码分散，拉升阻力可能较大。
风险点3：舆情中提及公司历史违规记录及重组相关风险提示，需保持关注。

✨ 利好催化:
利好1：公司被市场定位为AI服务器HDI核心供应商，受益于AI产业发展。
利好2：2025年前三季度扣非净利润同比暴涨407.52%，业绩表现强劲。

📢 最新动态: 【最新消息】舆情显示公司是AI PCB微钻领域龙头，
深度绑定全球头部PCB/载板厂。2月5日主力资金净卖出3.63亿元，
需关注后续资金流向。

---
生成时间: 18:00
```

### 大盘复盘

```
🎯 2026-01-10 大盘复盘

📊 主要指数
- 上证指数: 3250.12 (🟢+0.85%)
- 深证成指: 10521.36 (🟢+1.02%)
- 创业板指: 2156.78 (🟢+1.35%)

📈 市场概况
上涨: 3920 | 下跌: 1349 | 涨停: 155 | 跌停: 3

🔥 板块表现
领涨: 互联网服务、文化传媒、小金属
领跌: 保险、航空机场、光伏设备
```

---

## ⚙️ 配置说明

完整环境变量、模型渠道、通知渠道、数据源优先级、交易纪律和部署说明请参考 [完整配置指南](docs/full-guide.md)。

---

## 🖥️ Web 界面

![Web 工作台](sources/fastapi_server.png)

Web 工作台提供配置管理、任务监控、手动分析、历史报告、回测、持仓管理、智能导入和浅色 / 深色主题。启动方式：

```bash
python main.py --webui
python main.py --webui-only
```

访问 `http://127.0.0.1:8000` 即可使用。更多细节见 [本地 WebUI 管理界面](docs/full-guide.md#本地-webui-管理界面)。

---

## 🤖 Agent 策略问股

配置任意可用 AI API Key 后，Web `/chat` 页面即可使用策略问股；如需显式关闭可设置 `AGENT_MODE=false`。

- 支持均线金叉、缠论、波浪理论、多头趋势等内置策略
- 支持实时行情、K 线、技术指标、新闻和风险信息调用
- 支持多轮追问、会话导出、发送到通知渠道和后台执行
- 支持自定义策略文件与多 Agent 编排（实验性）

> Agent 具体参数、多 Agent 模式和预算护栏见 [完整指南](docs/full-guide.md) 与 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)。

---

## 📄 License

[MIT License](LICENSE)

本项目基于 MIT 协议开源，欢迎自由使用和二次开发。

---

## 🗺️ 项目规划

查看已支持的功能和未来规划：[更新日志](docs/CHANGELOG.md)
