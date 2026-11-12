# chat-twin

聊天分身：用自己的口吻聊天。已完成 [Issue #1](https://github.com/bmdy6/chat-twin/issues/1) 第1轮公开样本清洗；本分支实现第2轮本地检索 MCP。第3轮草稿引擎尚未开始。

## 第2轮检索：安装与启动

只检索公开样本，不生成草稿、不发送消息，也不存查询。需要 Python 3.11+ 与 uv。在仓库根目录执行：

```bash
uv sync --locked
# 首次可联网准备本地模型；已有缓存则直接复用
uv run python -m scripts.build_index
# 缓存就绪后可以离线重建；不调用 LLM
uv run --offline python -m scripts.build_index --offline
uv run --offline --no-sync chat-twin-mcp
```

`data/index/` 已提交离线索引（788条），无需每次重建。模型二进制在忽略的 `data/cache/models/`，不进 Git。服务启动强制 `local_files_only=True`、CPUExecutionProvider、2线程，加载一次后复用；缓存缺失、索引过期或损坏会明确报错，查询路径不会自动下载或调用远程 API。`--index-dir` 的父目录必须包含匹配的 `samples.jsonl`。wheel 安装时使用下面的绝对资源路径。

### MCP 工具

- `search_samples(message, stage=None, scene=None, k=5)`：返回 `id / stage / context / her / reply / goal / score`。`stage` 为1–7，检索当前阶段及相邻阶段（±1）；`scene` 为 online/offline/unknown，精确过滤。省略过滤条件则检索全部。`k` 为1–50，最多返回两路候选并集；空白或纯标点输入、无符合条件的样本返回空列表。消息最多10000字符。
- `list_stages()`：返回每个数字阶段的总数，以及各上游原名和对应条数；两个阶段5名称各自保留。

只用 `her + 换行 + context` 建词项和向量索引，reply/goal仅随结果返回。jieba 默认精确分词 + BM25Okapi，与本地 `BAAI/bge-small-zh-v1.5` 余弦相似度各取前30；stage/scene在两路截断前过滤。BM25无正匹配分的项不注入词法候选。RRF等权合并，常数60，按 `sum(1/(60+rank))` 计分；分数是排序分数，不是概率。同分按原样本顺序稳定排序。

### Claude Code 配置

在仓库根目录、完成 `uv sync` 和模型准备后，执行以下命令注册本地 stdio 服务：

```bash
claude mcp add --transport stdio chat-twin -- \
  "$PWD/.venv/bin/chat-twin-mcp" \
  --index-dir "$PWD/data/index" --model-cache "$PWD/data/cache/models"
```

可用 `claude mcp get chat-twin` 查看配置；在 Claude Code 中调用 `list_stages` 或搜索“今天工作累不累”。[Claude Code 官方配置说明](https://code.claude.com/docs/en/mcp#option-3-add-a-local-stdio-server)。

### Codex 配置

在 `~/.codex/config.toml` 增加以下配置，把路径替换成当前仓库的绝对路径（不能用未展开的 `~`）：

```toml
[mcp_servers.chat-twin]
command = "/absolute/path/chat-twin/.venv/bin/chat-twin-mcp"
args = ["--index-dir", "/absolute/path/chat-twin/data/index", "--model-cache", "/absolute/path/chat-twin/data/cache/models"]
startup_timeout_sec = 30
tool_timeout_sec = 10
```

配置语法依据 [OpenAI 官方 MCP 文档](https://developers.openai.com/codex/mcp)。这里仅提供配置方式，本轮未修改任何客户端全局配置，也未打开客户端界面。

## 固定评测与验证

`evals/retrieval.jsonl` 用种子2026111202从非空 her 的保留样本均匀抽50条，由 DeepSeek `deepseek-flash`、`thinking.type=disabled` 一次改写后固定提交。只把 her 发给模型，不发送 context/reply/goal；参数、来源摘要、usage见各行及 `evals/retrieval.meta.json`。生成入口 `uv run python -m scripts.generate_eval` 在文件已存在时拒绝覆盖；日常评测只读固定文件，无 LLM、无 key、无 API。

```bash
uv run --offline python -m scripts.eval_retrieval
uv run --offline python -m scripts.smoke_mcp
uv run --offline pytest -q
uv run --offline ruff check chat_twin scripts tests
uv run --offline ruff format --check chat_twin scripts tests
uv run --offline python -m compileall -q chat_twin scripts
uv build
```

首次评测无stage/scene过滤，Recall@5为42/50 = **0.84**；默认BM25参数、两路各30、RRF常数60已过线，未调参、未换评测集。核心检索和完整 stdio 往返延迟分开记录在 `evals/baseline.json`（首次）、`evals/results.json`（最终）与 `evals/mcp-smoke.json`，失败案例及解释见 [检索评测报告](evals/REPORT.md)。这衡量原ID命中，不等于回复质量或真实聊天效果；部分改写有语义歧义，留在固定集并列出给Claude审查。

`tests/test_offline.py` 在全新子进程阻断DNS、TCP连接和UDP发送，加载真实模型并查询；即使依赖吞掉连接异常也判失败。另测空缓存必须本地失败、不能下载。真实模型/stdio测试在未准备模型的全新机器会明确skip；本机交付运行不skip。普通检索单测使用合成向量，不需要联网。stdio smoke通过官方MCP客户端验证initialize、两个工具、过滤、非法参数、未知工具和关闭，不启动任何App。

## 第1轮清洗：运行与复现

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)；首次运行需联网下载固定来源、本地向量模型，并调用 DeepSeek API。

```bash
uv sync --locked
uv run pytest -q
uv run python scripts/clean.py --pilot
```

查看 `data/pilot.jsonl` 的 20 条完整输入、标签和最终决策，核对 `data/PILOT.md`。返工1的试跑覆盖两种来源、16种阶段原名及PR审查点名的4条回归样本。只有检查提示词有效后才能启动全量；提示词变化后旧批准失效。批准文件的字段为 `count: 20`、`prompt_sha256`、`pilot_sha256`、`reviewer`、`status`、`note`，使用当前文件的 SHA-256。例如人工核对完成后用 Python 更新两个摘要：

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
- 规则顺序为 broken → vulgar → manipulative → not_sendable。规则判断前，只剥离回复首尾≤15个Unicode字符、可确认为语气/表情/动作说明的括号，记录 `stripped_annotation`（多项以换行分隔）和完整 `original_reply`；剩余回复重新检查规则、60字限制和LLM。中间括号、长标注、多轮/条件剧本不剥离，仍按不可直接发送处理；不改写台词。
- 阶段数字按上游原编号，名称原样保留：两个阶段5仍分别叫“升温推进”“约会实战”。示例库“挽回/冷激活”无编号，暂映射7并 review。不同来源的同号阶段名称可能不同，未擅自统一。
- 无台词或纯括号状态描述的 her 设为空，原描述并入 context。示例未提供 goal，存空字符串；why 完整保留。
- 最后仅对尚未删除的项，用 fastembed 的 `BAAI/bge-small-zh-v1.5`、CPU 编码 `her + 换行 + reply`，L2 归一化后余弦相似度严格 >0.92 删除，保留输入顺序中的第一条。记录 `duplicate_of` 和 `similarity`；不通过被删项形成传递重复链。
- `data/REPORT.md` 列出阶段统计、保留/删除各30条原文（返工1新随机种子2026111201，前版为20261112）、限制与待审点；MIT 许可全文见 `data/ATTRIBUTION.md`。

清洗脚本保留原入口。检索服务现已支持 wheel/sdist 构建；测试包含解析、两个阶段5、四类删除各至少2例、边界、去重、标签契约和离线/API失败路径。

第1轮返工已随 PR #2 合并。第2轮开头按 Issue 当前状态修正两条遗留：`qs-c-3386` 去掉末尾「（坚持目送）」后保留；`qs-c-3044` 改判 vulgar。保留788条、删除649条，其余1435条不变。原始模型审计不改写，规则判定和 REPORT 已同步。
