# 第 1 轮样本清洗 Implementation Plan

> **For agentic workers:** Execute inline with executing-plans. No delegation required.

**Goal:** 完成 Issue #1 第 1 轮，生成可审查数据并提 PR，不合并。
**Architecture:** 固定上游 SHA 的两种解析器，规则先行，DeepSeek 标签可校验缓存，最终候选按来源顺序做本地向量去重；确定性报告抽样。
**Tech Stack:** Python 3.11+、uv、httpx、fastembed、numpy、pytest。

## Global Constraints

- 仅第 1 轮，不实现检索 MCP、草稿引擎或真实聊天接入。
- 上游固定 fd762df2e0ce4b8ec68b0087ba7178761045bd1a；1395 + 42 条。
- DeepSeek deepseek-flash，thinking.type=disabled；仅从本机 vault 读取个人 key。
- raw、缓存、密钥不提交；不启动 Mac App 或界面。
- 拿不准保留且 review=true；先审查 20 条试跑再允许全量。

## Tasks

- [ ] 1. `tests/test_clean.py` 先覆盖两种解析、两种阶段5、无台词、四类删除各2例、重复阈值和仅对已保留项去重；运行 `uv run pytest` 验证缺少实现时失败。
- [ ] 2. `scripts/clean.py` 实现 download_sources / parse_candidates / parse_examples / rule_reason / classify / deduplicate / run。`prompts/label.txt` 外置边界与 JSON 契约；运行测试至通过。
- [ ] 3. `uv run python scripts/clean.py --pilot` 分层试跑20条；逐条核对标签写 `data/PILOT.md`。确认后执行 `--full`，缓存按内容+提示词摘要区分，避免重复付费。
- [ ] 4. 输出 samples、dropped、REPORT、ATTRIBUTION，报告阶段原数/保留/删除、固定种子抽样各30条；核对总数、完整字段、去重阈值、无原件和密钥。
- [ ] 5. 更新 README，运行 pytest、ruff、compileall、uv build（若构建适用）、离线缓存重跑及 git diff --check。完成独立复核后 push，按指定标题/正文开 PR 到 main，不合并。
- [ ] 6. 归档中文总结并回读，最后创建 DONE；只把有新增价值的通用经验经知识库受保护写入暂存。

## Condition matrix

| 条件 | 结果 |
|---|---|
| reply 缺失 | broken |
| 明确低俗、操纵、不可发 | 按规则/LLM 删除并记录依据 |
| LLM 低置信/冲突/结构错误或调用耗尽 | 保留并 review；不冒充正常标签 |
| 仅 bad 中含坏话 | 不据此删好回复 |
| 无台词 | her=""；描述并入 context |
| 非编号挽回分类 | stage=7、原 stage_name、review=true |
| 向量 >0.92 且前项已保留 | duplicate，记录前项及相似度 |
| 下载失败/内容摘要不符 | 停止发布产物，不覆盖已生成正式数据 |
