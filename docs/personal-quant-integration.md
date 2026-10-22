# personal-quant 数据接入与整合说明

## 功能归属

本次以 Financial_Market_Situation_Awareness 为主项目，保留已有多市场单股分析、行业宏观/成分股/CIO 研报、Web、Bot、通知、调度、历史报告、Agent、持仓和既有回测入口。新增可选只读桥接，将 personal-quant 的数据能力接入现有 DataFetcherManager 与分析管线。

| 能力 | 负责模块 | 本次处理 |
|---|---|---|
| 线上多源行情与 fallback | `data_provider/` | 保留；canonical 失败降级必须显式 opt-in |
| 已最终化 ETF 日线 | personal-quant `ParquetMarketRepository` | 读取 v3 revision schema 的显式 Longbridge 来源 |
| 经济/系统时点与 revision | `src/services/personal_quant.py` | 先按 PIT 与 cutoff 筛选，再选最新 observation |
| ETF 元数据 | personal-quant `MetadataResolver` | 逐字段解析，保留 freshness/conflict/provenance |
| 单股、行业、Agent 历史日线 | `src/core/pipeline.py`、`history_loader.py` | worker 绑定 canonical snapshot，跳过 mutable DB 日线缓存 |
| 报告可追溯性 | `market_snapshot.data_provenance`、`data_sources` | 追加快照、来源与时间语义 |
| 行业聚合可靠性 | `pipelines/` | 修复未执行的请求延迟、评分尺度猜测与共享管线初始化竞争 |

源版本：旧项目基线 `5e4014a5e5ada7e4824e8824940948dd90cfbfe3`；桥接依赖固定到 personal-quant `4508ca6cd8204976b53c9f982ecfbc34421ce744`。冻结 Universe 和未审批的 vehicle evidence pack 留在原项目，本项目不把 candidate 提升为生产映射。

## 安装和配置

使用 Python 3.11+，先安装本项目 `requirements.txt`，再安装：

```bash
python -X utf8 -m pip install -r requirements-personal-quant.txt
```

本地联调也可 `python -m pip install -e <personal-quant-checkout>`；不通过 `sys.path` 注入源码，不复制另一套 repository 实现。

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `PERSONAL_QUANT_CANONICAL_DIR` | 空，关闭 | 已存在的 canonical 根目录，内部包含 `market_bars/` 和可选 `instrument_metadata.sqlite3` |
| `PERSONAL_QUANT_METADATA_POLICY` | 空 | personal-quant 的 `configs/metadata_resolution.yaml` 绝对路径 |
| `PERSONAL_QUANT_ALLOW_FALLBACK` | `false` | canonical 日线失败时，是否允许旧在线 provider 链 |
| `PERSONAL_QUANT_MAX_STALE_SESSIONS` | `1` | 当前视图下，本地最新 K 线最多可落后最近已收盘交易日几个交易日；超过即视为读取失败 |

配置可放在本项目 `.env` 或进程环境中。桥接不需要 Longbridge 凭证，不加载 personal-quant 的 `.env`。原 Longbridge 线上适配器仍使用它自己的凭证配置。

只注册内地 ETF（SH: 51/52/56/58，SZ: 15）；LOF、个股、港股、美股走原有流程。显式 suffix 与代码冲突时 fail fast。未配置 bridge 不导入可选 `etf_quant` 包。

## 数据与时间语义

`data_time` 是交易日收盘时点，`available_time` 来自源的 EOD availability policy，`ingest_time` 是实际观察/下载时间。personal-quant 负责 finalized-only promotion；bridge 不读取 raw cache，也不重抓数据。

所有查询显式 source=`longbridge`，仅使用 unadjusted canonical 值。OHLC 使用 Decimal 字符串导出，映射到旧分析 DataFrame 时转换为 float；volume 沿用源单位，turnover 映射为 amount，不隐式乘 100。

| 模式 | eligibility | 用途 |
|---|---|---|
| ECONOMIC | `available_time <= as_of` 且 `ingest_time <= research_data_cutoff` | 历史经济时点的数据视图 |
| SYSTEM_REPLAY | 上述条件再加 `ingest_time <= as_of` | 还原系统实际见过的数据 |

cutoff 在 revision selection **之前**生效。T2 修订晚于 cutoff 时，仍能选回 T1 的旧值。相同最新 ingestion time 存在不同 value hash 时 fail fast，hash 不决定哪个价格更新。Economic 查询必须显式提供 cutoff；System Replay 未传 cutoff 时固定为 as_of。

`HISTORICAL_LATEST` 表示今天取得的历史最新值，不等于 provider historical vintage。导出保留 `historical_data_semantics`。`dataset_snapshot_id` 是 query contract、选中 observations 和解析后元数据的 SHA-256，配合输出 JSON 可审计本次数据集；它不是源文件的物理备份。

桥接不自动更新数据。应用的当前视图会用交易日历计算最近已收盘交易日（`expected_trade_date`），本地最新 K 线落后超过 `PERSONAL_QUANT_MAX_STALE_SESSIONS` 个交易日时按读取失败处理：默认停止分析，开启 fallback 时改走旧在线链路。provenance 记录 `expected_trade_date` 与 `missing_sessions`。导出 CLI 和显式指定 `end_date` 的历史查询不做此检查。空结果不被零值或 forward-fill 掩盖。

`dataset_snapshot_id` 绑定查询条件（含 as_of），每次查询都不同，用于审计“这次查询”；`content_hash` 只对选中的 observations 和解析后元数据求哈希，用于判断两次分析是否使用同一份数据。

## 复权口径

canonical 日线为**不复权**价格（`adjust_type: none`、`dividend_adjusted: false`），旧在线链路的 ETF 日线为前复权。两者在除息日附近不可直接比较：

- 分红通常只造成 1%–3% 的跳空，桥接无法识别，均线会有对应幅度的偏差。
- 份额折算等公司行动造成的跳空超过任何内地 ETF 的涨跌幅限制（检测阈值 21%），会记录在 `price_discontinuities`，应用分析只使用最近一次跳空之后的 K 线（`analysis_window_start`），避免新旧价格尺度混算均线。
- 报告 prompt 中会注明日线截止日和不复权口径。

彻底解决需要 personal-quant 入库复权因子；在此之前，对复权敏感的研究请使用导出数据自行处理。

## 应用与缓存隔离

应用只自动使用当前时点的 `SYSTEM_REPLAY` 视图。修订版本按日期范围一次读取，不再逐日扫描 Parquet。canonical 数据在 worker 中通过 ContextVar 绑定，技术分析和 Agent history tools 使用同一 observation 集，不再从 `stock_daily` 取另一版本。请求结束恢复上下文，避免并发标的相互污染。

canonical 输入不写入本项目 mutable 日线缓存；报告和新闻仍存入现有应用数据库。Agent 初始上下文、行业精简摘要和 `AnalysisResult.market_snapshot` 带 provenance。`data_sources` 追加 source 与 snapshot id，并通过 `to_dict()` 导出。API 变化为已有对象中的追加信息，无字段删除或枚举变更。

实时行情可能增强当前分析指标，不写回 canonical。当前新闻、舆情、模型知识和实时 quote 独立于 PIT 日线，因此完整 LLM 报告和原有回测不因 bridge 启用而成为历史 PIT 研究。历史数据请求使用独立导出命令。

## 历史导出

```bash
python -m src.services.personal_quant \
  --canonical-dir /path/to/personal-quant/data/canonical \
  --metadata-policy /path/to/personal-quant/configs/metadata_resolution.yaml \
  --symbol 510300.SH --start-date 2019-01-01 --end-date 2019-12-31 \
  --as-of 2019-12-31T16:00:00+08:00 --mode economic \
  --research-data-cutoff 2026-10-22T00:00:00+08:00 \
  --output data/pit/510300_2019.json
```

输出包含 `provenance`、`bars`、`metadata`。时间参数必须有时区；bar 保留 observation_id/value_hash/三时间戳，metadata 保留 field resolver policy id、状态和候选来源。查询不调用 LLM、新闻、通知或在线 provider。

`--output` 必须位于 canonical 目录之外，且文件必须尚不存在；导出不会覆盖已有快照或源文件。

metadata SQLite 以 `mode=ro` 打开，复用 upstream eligibility 与 resolver。缺少数据库或未配置 policy 时明确 `UNAVAILABLE`；配置的 policy 不存在或 schema 错误会保留错误。冲突遵循 upstream resolver 的状态及 fail-fast 规则。

## 部署和回退

Docker 使用现有构建流程，在自定义镜像额外安装 `requirements-personal-quant.txt`，将 canonical 目录和 policy 文件作为只读 volume 挂载。环境变量使用容器路径；默认镜像不包含可选包。

GitHub Actions 读取真实 canonical 文件时需使用可访问该数据的 runner 或数据制品；仅配置路径不会上传本地文件。仓库 CI 安装 pinned bridge dependency 后运行临时 fixtures，不需要线上密钥。

清空 `PERSONAL_QUANT_CANONICAL_DIR` 并重启即可恢复原 provider/cache 路径，源文件始终只读。代码回滚可 Git revert integration commit，无需回滚数据 schema。

## 验证范围

离线 fixtures 覆盖：晚下载历史的两种 PIT 模式、cutoff 前修订恢复、同时间冲突、快照稳定性、Decimal、源目录只读、metadata UNKNOWN/SQLite 只读、默认 fail-closed/显式 fallback、缓存隔离、Agent 绑定快照、代码边界、行业评分与 delay、数据过期拦截、公司行动跳空截断、content_hash 稳定性、单次范围扫描、关闭桥接时的代码容错。

真实行情刷新、LLM 服务、通知和 Docker/桌面发布需要各自环境。本次接入测试不发送通知或下单。
