# 第 2 轮检索 MCP 实施计划

目标：只完成 Issue #1 当前状态的两条修正与第 2 轮，PR 到 main，不合并。

技术约定已由 Issue 明确：Python 3.11+ / uv / FastMCP stdio / jieba BM25 / fastembed BAAI/bge-small-zh-v1.5 / 两路 top30 + RRF。无需新增方案审批。在用户指定的干净仓库独立分支执行，保留原 main。

- [x] 遗留修复：先在 tests/test_clean.py、tests/test_dataset.py 写失败回归；scripts/clean.py 增加短动作识别和上下文性暗示规则；仅调整两个样本及 REPORT，保留标签来源。pytest、ruff、diff 复核后独立提交。
- [x] 固定评测：scripts/generate_eval.py 用固定种子从非空 her 保留样本抽50条；仅向 DeepSeek flash disabled thinking 发送 her，要求语义等价改写；保存 query、expected_id、来源、请求参数摘要和 usage。evals/retrieval.jsonl 单独固定提交，运行评测不导入生成器、不访问 API。
- [x] 检索：chat_twin/retrieval.py 提供索引构建和加载、jieba BM25、向量 top30、RRF、stage±1及精确scene过滤；scripts/build_index.py 离线产物在 data/index/；FastMCP 在 chat_twin/server.py 暴露且只暴露两个工具，打包为 chat-twin-mcp。
- [x] 质量：先写检索与边界测试，验证过滤发生在各路排名前、稳定排序、返回字段、损坏/过期索引拒绝、空消息/非法参数；用实际缓存模型在阻断 DNS/socket 的新子进程验证加载及查询均无联网尝试；测试 stdio MCP initialize/list/call。
- [x] 评测：scripts/eval_retrieval.py 固定50条直接无过滤检索（不传答案 stage），top5 recall >=0.8；初次不达标则先保存失败分析，再调检索参数，不改50条。在本机预热后重复查询测 p95 <300ms，结果含硬件/版本/次数/样本及索引摘要。先无提示评测，阶段过滤另测。
- [x] 交付：README 写构建、模型缓存、离线失败语义、Claude Code/Codex stdio 配置；pytest/ruff/构建和真实 MCP smoke；最终 diff 按 Issue 重新审查；提交、push、PR 标题固定，正文包含实现/指标/调参/测试/Claude 待审。中文总结写指定归档目录，最后创建 DONE。

边界：无第3轮、无联系人/草稿、无真实聊天、无 UI。模型首次下载允许联网，运行检索不得下载。stage 省略搜所有；scene 省略搜所有，unknown 是精确值；无候选返回空列表；索引缺失/源数据变化/模型缓存缺失明确失败并提示构建，不静默联网或退化。

执行说明：实现、测试与指标已完成；最后一项的外部交付按用户既有授权继续执行，实际PR链接和归档交付以最终总结为准。
