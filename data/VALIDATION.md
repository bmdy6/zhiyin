# 第 1 轮验证记录

- `uv sync --locked`：依赖锁与环境一致。
- `uv run pytest -q`：38 passed，包含两种解析、两个阶段5、四类删除各至少2例、否定句、边界绕过、占位/动作脚本、缓存/提示词失效和API异常回退，以及提交数据的分区、字段、标签、REPORT抽样不变量。
- `uv run ruff check scripts tests`：通过。
- `uv run ruff format --check scripts tests`：通过。
- `uv run python -m compileall -q scripts`：通过。本仓为脚本项目，没有应用/分发包构建。
- `uv run --offline python scripts/clean.py --full --offline`：通过，全部8个既有数据文件SHA-256字节一致（包含样本、删除、标签、试跑批准及报告）；无新API调用。
- 真实向量复核：44条重复均指向保留项，重算余弦与记录一致；760条保留集任意两条余弦不超过0.92。
- `git diff --check`：通过；提交前逐文件检查原件排除、真实vault密钥字面量泄漏。
- DeepSeek：1437条全部拿到合法响应，响应model均为deepseek-flash，thinking关闭，失败降级0；试跑先20条，校准提示词后再全量；有效结果可从labels.jsonl回查。

仅完成第1轮工程交付。Claude需独立审查REPORT的30保留+30删除，误删或漏删≤3才算人工验收通过；本记录不冒充Claude结论。不合并、不开始第2或3轮。全程只用CLI和API，没有打开Mac App或界面。
