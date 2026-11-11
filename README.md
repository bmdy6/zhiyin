# chat-twin

聊天分身：用自己的口吻聊天。本 PR 只实现 [Issue #1](https://github.com/bmdy6/chat-twin/issues/1) 第 1 轮的公开样本清洗。第 2 轮检索 MCP、第 3 轮草稿引擎尚未实现。

## 运行与复现

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)；首次运行需联网下载固定来源、本地向量模型，并调用 DeepSeek API。

```bash
uv sync --locked
uv run pytest -q
uv run python scripts/clean.py --pilot
```

查看 `data/pilot.jsonl` 的 20 条完整输入、标签和最终决策，核对 `data/PILOT.md`。只有检查提示词有效后才能启动全量；提示词变化后旧批准失效。批准文件的字段为 `count: 20`、`prompt_sha256`、`pilot_sha256`、`reviewer`、`status`、`note`，使用当前文件的 SHA-256。例如人工核对完成后用 Python 更新两个摘要：

```python
import hashlib
import json
from pathlib import Path

approval = {
    "count": 20,
    "prompt_sha256": hashlib.sha256(Path("prompts/label.txt").read_bytes()).hexdigest(),
    "pilot_sha256": hashlib.sha256(Path("data/pilot.jsonl").read_bytes()).hexdigest(),
    "reviewer": "填写实际检查者",
    "status": "reviewed-before-full",
    "note": "填写检查结论",
}
Path("data/pilot-approved.json").write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
```

```bash
uv run python scripts/clean.py --full
# 同一输入/提示词复用标签、向量缓存；不会再次付费。
uv run python scripts/clean.py --full --offline
uv run ruff check scripts tests
uv run ruff format --check scripts tests
uv run python -m compileall -q scripts
```

脚本只用个人 vault 的 `model-providers.deepseek.DEEPSEEK_API_KEY.value`，不读取公司 key。API 固定 `https://api.deepseek.com/chat/completions`，模型 `deepseek-flash`，`thinking.type=disabled`，参数依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/guides/thinking_mode/)。密钥只进入 HTTPS 认证头，不写文件或日志。

## 数据与清洗约定

- 固定来源 `tomwong001/qingsheng-skill@fd762df2e0ce4b8ec68b0087ba7178761045bd1a`：候选1395条、示例42条。下载时检查内置 SHA-256；chatlogs 不下载。
- `data/raw/`、`data/cache/` 被 Git 忽略。原件、模型、逐条 API 缓存、向量矩阵均只留本机。`--offline` 要求它们都已缓存，否则失败，不联网。
- `data/samples.jsonl` 是保留集；`data/samples.dropped.jsonl` 是删除集，含 reason 和判断依据。`data/labels.jsonl` 保留所有1437条的模型标签、token用量、输入摘要，不含密钥。
- 先规则，后 LLM。全部条目均打标签；规则优先；没有规则命中的情况下，模型 `review=true` 或置信度 <0.9 一律保留并标 review。称呼、隐喻及互动意图不明确时另标 review（词表见 `apply_label`）。最多3次尝试，API/标签校验失败也保留 review，不把失败写入成功缓存。多重原因只计一个，以规则先后顺序为准。
- 规则顺序为 broken → vulgar → manipulative → not_sendable。括号动作、策略说明和 >60 Unicode 字符的回复按原文删除，不自动裁剪/改写。
- 阶段数字按上游原编号，名称原样保留：两个阶段5仍分别叫“升温推进”“约会实战”。示例库“挽回/冷激活”无编号，暂映射7并 review。不同来源的同号阶段名称可能不同，未擅自统一。
- 无台词或纯括号状态描述的 her 设为空，原描述并入 context。示例未提供 goal，存空字符串；why 完整保留。
- 最后仅对尚未删除的项，用 fastembed 的 `BAAI/bge-small-zh-v1.5`、CPU 编码 `her + 换行 + reply`，L2 归一化后余弦相似度严格 >0.92 删除，保留输入顺序中的第一条。记录 `duplicate_of` 和 `similarity`；不通过被删项形成传递重复链。
- `data/REPORT.md` 列出阶段统计、保留/删除各30条原文（固定随机种子20261112）、限制与待审点；MIT 许可全文见 `data/ATTRIBUTION.md`。

本仓目前是脚本项目，无应用或分发包构建；使用 compileall 检查字节码编译。测试包含解析、两个阶段5、四类删除各至少2例、边界、去重、标签契约和离线/API失败路径。

Claude 仍需独立抽查 REPORT 中的60条，误删或漏删 ≤3 才算第1轮人工验收通过。这个 PR 不合并，也不开始后两轮。
