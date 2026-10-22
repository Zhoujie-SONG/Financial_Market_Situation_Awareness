# 整合验证记录 — 2026-10-22

本次以旧项目 `5e4014a5e5ada7e4824e8824940948dd90cfbfe3` 为基线，接入 personal-quant `4508ca6cd8204976b53c9f982ecfbc34421ce744` 的已实现数据接口。

## 已执行

- Windows / Python 3.11 专用虚拟环境，安装现有后端依赖；未修改全局 Python 环境。
- 全量离线后端测试：`1767 passed, 2 deselected, 36 warnings`，158.44 秒。
- 新增桥接与行业 contract：36 项通过，使用临时 Parquet / SQLite fixtures。
- 全仓库 Flake8 critical checks（E9/F63/F7/F82）：通过。
- 所有修改 Python 文件的 py_compile：通过。
- Web `npm ci`、`npm run lint`、`npm run build`：通过；构建的 static 文件为本地产物，不提交。
- 真实 `main.py --help`、PIT export CLI help 及 JSON snapshot 导出 fixture：通过。
- `git diff --check`：通过；原 personal-quant Universe v1 SHA-256 仍为 `7a72fcb6ae8ab60cc2cbf89bf6276251c626e94e8dcd1169f2b08e4564c9ca2e`。

## 本机测试环境说明

本机 Anaconda 枚举 Windows 证书库时触发 `ssl.SSLError: ASN1 NOT_ENOUGH_DATA`，使 LiteLLM/aiohttp 在 import 阶段失败。完整测试和真实 CLI import smoke 使用 **当前进程的 certifi CA bundle**；TLS 验证仍然开启，未修改系统证书库、未关闭证书校验、未将环境 workaround 写入产品代码。

复现该环境下的离线测试：

```bash
python -X utf8 -c "import ssl, certifi, functools; ssl.create_default_context = functools.partial(ssl.create_default_context, cafile=certifi.where()); import pytest; raise SystemExit(pytest.main(['-q', '-o', 'addopts=', '-m', 'not network', '--tb=short']))"
```

一般环境直接执行 `python -m pytest`。Python requirements 文件含中文注释，因此 Windows 安装命令明确使用 `-X utf8`。

## 限制和遗留事项

- 当前工作区的 personal-quant canonical 目录尚无实际 Parquet/SQLite 数据；新增接口已通过 fixtures，真实行情联调需先运行源项目 ingestion。
- 未调用真实 LLM、未发送通知；未构建 Docker 镜像或 Electron 安装包。
- npm 安装使用原 lockfile，报告既有依赖审计告警 23 项（1 low、6 moderate、16 high）。本次未执行会改动依赖树的自动 audit fix；应单独评估升级与客户端回归。
- Vite 构建保留既有 large-chunk 和 Browserslist 数据年龄提示，未影响构建通过。
- 后端测试保留既有 Pydantic deprecation / collection warnings，未因本次桥接变更删除旧 API 字段。

## 回滚

清空 `PERSONAL_QUANT_CANONICAL_DIR` 并重启可恢复旧 provider/cache 路径。需要代码回滚时 Git revert 本次 integration commit；无需修改或回滚 personal-quant 源数据。
