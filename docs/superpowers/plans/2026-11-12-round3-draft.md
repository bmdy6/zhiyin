# 第3轮草稿引擎实施计划

目标：完成 Issue #1 当前状态和第3轮验收；沿用既有 Python/httpx/官方 MCP，不发送消息。

边界：只在 codex/round3-draft worktree 实现；DeepSeek deepseek-flash + thinking disabled；文风仅运行时读取，不记录请求、不提交档案；联系人默认 ~/.chat-twin，所有测试只用临时目录；不开 App，不合并。

## 文件与执行顺序

- [x] `tests/test_draft.py`、`tests/test_contacts.py`：先验证输入、风险交回、自检、失败重写上限、friend、三天/手动升级、CLI 和持久化边界。
- [x] `chat_twin/draft_models.py`、`safety.py`：结构化契约、确定性边界。unknowns >=3 交回；语义风险交给模型决策/自检补足。
- [x] `chat_twin/contacts.py`：总文档、日期去重和独立档案；只记录输入摘要，草稿不算已发送。原子写入与本地锁；保留手工档案段落。
- [x] `chat_twin/draft.py`、`llm.py`、`sample_client.py`、`cli.py`：分析→决策→真实 stdio MCP 检索→写→本地及模型自检；不通过重写一次，再不过交回。API/协议失败保守交回，错误不输出敏感正文。
- [x] `prompts/{analyze,decide,write,check}.txt`、`profiles/{example,contact}.md`、`vendor/tong-jincheng/`：提示词分别维护；完整固定上游 SKILL/LICENSE，只借真诚原则，不激活角色扮演。
- [x] `tests/fixtures/draft.jsonl`、`scripts/eval_draft.py`：固定20段纯合成对话，10正常+5交回+5熟人；真实 DeepSeek 与 stdio MCP，临时联系人；只保存生成输出及非敏感评测元数据。
- [x] README、构建资源：CLI 用法、隐私/风险边界、复现命令；wheel 安装后在仓库外验证资源和 CLI。
- [x] 全量 pytest、ruff、compile、build、真实20段评测；审查完整diff，确认没有档案/key/真实数据。
- 交付操作（状态以PR和本地归档总结为准）：commit/push，PR 到 main，标题按用户要求，包含5段完整输出、通过情况、约定与 Claude 审查点；不合并。归档总结并最后创建 DONE。

## 条件与副作用矩阵

|条件|结果|联系人更新|
|---|---|---|
|正常/熟人|1–3条、每条≤40字的草稿及理由|只记输入摘要/当天日期|
|身份/钱/见面联络/严重情绪/承诺隐私|交给你，无草稿|只记本次输入概况|
|unknowns ≥3 / 模型判风险|交给你|只记输入概况|
|首次自检失败|只重写一次|最终仅更新一次|
|二次自检失败/API或MCP错误|交给你，不显示失败草稿|仅已有分析可信时记摘要，否则固定未生成提示|
|输入非法/联系人路径非法|非零退出，不调用API|不写|
|同一天多次/第三个不同日期/手动|去重日期/自动档案/手动档案|保留原档案人工内容|

证据：离线单测覆盖确定性和失败路径；本机真实 stdio 与模型覆盖20段验收；合成集不能证明真实聊天的主观口吻合格，PR交Claude判断。

实现与验证结果：全量140项通过（0跳过），干净基线归档重放补丁同样140项通过；最终20段全部通过，首次完整17/20报告保留；安装wheel在仓库外实际生成草稿并手动升级通过。
