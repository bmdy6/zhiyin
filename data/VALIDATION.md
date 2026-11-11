# 第1轮返工1验证记录

首轮Claude抽查约5处误删/漏删，未达到≤3标准；本记录仅说明返工后的工程检查，人工复审仍待完成。

- `uv sync --locked`：通过。
- `uv run pytest -q`：73 passed。新增首尾短标注/中间括号、15/16字边界、纯标注与条件剧本、原文留痕、goal目的与否定句、审查4条回归及新种子抽样验证。
- `uv run ruff check scripts tests`、`uv run ruff format --check scripts tests`：通过。
- `uv run python -m compileall -q scripts`：通过。本仓为脚本项目，无应用或分发包构建。
- 从Git暂存区导出干净检出，仅复用现有Python依赖环境：71 passed、2 skipped；两项跳过仅因未携带data/raw缓存，其他测试不依赖密钥或联网。
- 1437条原始来源字段/台词核对：除93条纯标注剥离及3条复合表情/动作说明（合计96条）外未改写台词；original_reply与固定来源精确相同。全部当前标签缓存的ID、内容摘要、模型及关思考参数匹配。
- 1437条在新提示词下均取得合法DeepSeek响应，served_model均为deepseek-flash，失败降级0；先20条试跑（含审查4条），核对后再全量。
- 关键回归：qs-c-3388、qs-c-2781剥离后保留；qs-c-2545剥离后因goal规避抗拒而改判manipulative；qs-c-2418因“体面接受”的操纵目的由规则和LLM同时判manipulative。
- `uv run --offline python scripts/clean.py --full --offline`：完整离线复跑通过，9个数据文件SHA-256字节一致，不新增API调用。
- 真实向量复核45条duplicate指向、顺序和余弦值一致；788条保留集最大两两余弦0.904689270761，不超过0.92。
- `git diff --check`及暂存区检查通过；17个当前跟踪文件对照88个vault密钥字面量扫描无泄漏；data/raw、data/cache和docs/superpowers/plans均不在当前跟踪集。
- REPORT改用新随机种子2026111201，独立重抽30保留+30删除，测试核对抽样结果与JSONL一致。

最终解析1437条，保留788条、删除649条，review保留70条；vulgar19、manipulative160、not_sendable425、duplicate45、broken0。流程为规则后1012条（删除425）、LLM后833条（新增删除179）、去重后788条（删除45）。

仅第1轮；未合并、不开始第2或3轮。全程CLI/API，未打开Mac App或界面。Claude仍需独立复审新REPORT的60条，误删/漏删≤3才满足人工验收。
