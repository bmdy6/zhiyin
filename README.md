# 知音（zhiyin）

知音从你的微信聊天记录找过去遇到过的情境，结合沟通习惯给出回复草稿。每条草稿附来源和自检理由，由你自己决定是否复制发送。[需求与验收](https://github.com/bmdy6/zhiyin/issues/6)

## 安装与导入

需要 Python 3.11+ 和 uv。在仓库根目录执行：

```bash
uv sync --locked
# 首次准备本地向量模型。已有缓存可以跳过。
uv run --locked python -m scripts.build_index
uv run --locked --offline zhiyin import_wechat --prepare-search
uv run --locked --offline zhiyin tone_profile
```

- `import_wechat` 默认从 `~/Documents/knowledge-mcp/private/微信聊天/` 读取 `导出清单.json` 和清单指定的各会话 `chat.jsonl`。可以用 `--source` 指定同一格式的其他导出。导入按会话 ID、时间、方向、类型、内容和同秒重复次数识别消息。导出没有数据库消息 ID，同一份或扩充后的快照可重复导入。同一秒内两份独立截取的完全相同内容无法区分。
- 回复对关联六小时内上一条对方消息。区分同秒顺序。可读文本包括普通文本和引用回复的本次正文。`--prepare-search` 额外在本机预计算向量；省略时由首次检索计算。两种方式均沿用本地 BGE 编码器。
- `tone_profile` 生成 `~/.zhiyin/口吻.md`，统计发出的有效消息、文本长度、单条与连发、标点、语气词、表情和常用词，并按关系保留短句例子。终端只显示统计。口吻文件可在导入新数据或修改关系分组后重建。
- 存储目录 `~/.zhiyin/` 的权限为 0700，生成文件为 0600。首次使用时从存在的 `~/.chat-twin/` 复制旧文件，保留原目录，已有目标文件优先。旧命令 `chat-twin` 和 `chat-twin-mcp` 仍可调用，会在 stderr 提示更名。

### 排除与关系组

在 `~/.zhiyin/config.toml` 修改配置，下面的键都是合成示例。会话 ID 和代号可避免同名联系人混淆。

```toml
exclude_contacts = ["示例会话-A"]

[relationships]
"示例会话-B" = "朋友"
"示例会话-C" = "同事"
```

支持 `家人`、`朋友`、`同事`、`陌生人或办事`、`未分类`。导出没有关系信息且本地没有配置时，归为未分类。工具查询即时应用排除名单，再次导入时从消息和向量索引清理被排除的会话。草稿使用与当前可见消息和关系分组匹配的口吻统计。

## 查看近况、检索、起草

在 Claude Code 注册本地 MCP：

```bash
claude mcp add --transport stdio zhiyin -- \
  "$PWD/.venv/bin/zhiyin-mcp" \
  --index-dir "$PWD/data/index" --model-cache "$PWD/data/cache/models"
```

在 Codex 的 `~/.codex/config.toml` 填入相同的绝对路径：

```toml
[mcp_servers.zhiyin]
command = "/absolute/path/zhiyin/.venv/bin/zhiyin-mcp"
args = ["--index-dir", "/absolute/path/zhiyin/data/index", "--model-cache", "/absolute/path/zhiyin/data/cache/models"]
startup_timeout_sec = 30
tool_timeout_sec = 240
```

- `contact_brief(contact)` 接受已导入会话的备注、昵称、代号或 ID，返回首末时间、跨度、最近几条消息和根据近期对话识别的待确认约定。约定按文本线索估计，实际履行情况由本人确认。
- `search_my_replies(message, contact=None, k=5)` 检索「对方说过什么 → 我当时怎么回」，附日期。相似结果按同一联系人、同类已知关系、其他联系人排序。用第 2 轮的 jieba + BM25、BGE 向量和 RRF，词法匹配和向量门槛允许返回空结果。
- `draft_reply(contact, message, goal=None)` 对已导入的联系人起草 1–3 条可独立使用的回复，每条附参考编号和对应的复盘规则。找不到相似个人回复时才使用公开样本。身份、钱、见面、严重负面情绪和需要本人承诺的消息交回本人。六条规则经过语义自检，首次失败可重写一次。
- 公开辅助工具 `search_samples` 和 `list_stages` 沿用第 2 轮的行为。公开索引默认读取仓库的 `data/index/`，wheel 安装时请用 `--index-dir`、`--model-cache` 指定对应绝对路径。

CLI 也提供这些入口。带原文的结果只在你主动调用时返回；用输入文件可以在命令行提供对话上下文。

```bash
uv run --locked zhiyin contact_brief "示例联系人"
uv run --locked zhiyin search_my_replies "合成消息" --contact "示例联系人"
uv run --locked zhiyin draft_reply --contact "示例联系人" --input /path/to/chat.txt
```

`--input` 为 UTF-8，每行 `我:` / `她:` / `他:`，最后一条来自对方。也可用 `--message` 传单条新消息。导入、口吻统计和检索在本机运行。草稿会将待回复消息、必要的近况、选中的参考、口吻统计和 `~/.style-polish/spoken.md` 作为模型输入。口吻档案的真实例句留在本地。DeepSeek 沿用 vault 的个人密钥、`deepseek-flash` 和关闭思考的配置。项目日志不记录聊天、档案或请求正文。

## 本地检查

```bash
uv run --locked --offline pytest -q
uv run --locked --offline ruff check zhiyin scripts tests
uv run --locked --offline ruff format --check zhiyin scripts tests
uv build
```

微信功能的测试使用合成导出和模拟聊天模型。真实向量检索和 stdio 测试复用本地模型缓存。

## 固定评测与验证

`evals/retrieval.jsonl` 用种子2026111202从非空 her 的保留样本均匀抽50条，由 DeepSeek `deepseek-flash`、`thinking.type=disabled` 一次改写后固定提交。只把 her 发给模型，不发送 context/reply/goal；参数、来源摘要、usage见各行及 `evals/retrieval.meta.json`。生成入口 `uv run python -m scripts.generate_eval` 在文件已存在时拒绝覆盖；日常评测只读固定文件，无 LLM、无 key、无 API。

```bash
uv run --offline python -m scripts.eval_retrieval
uv run --offline python -m scripts.smoke_mcp
uv run --offline pytest -q
uv run --offline ruff check zhiyin scripts tests
uv run --offline ruff format --check zhiyin scripts tests
uv run --offline python -m compileall -q zhiyin scripts
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
