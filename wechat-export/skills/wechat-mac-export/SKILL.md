---
name: wechat-mac-export
description: "Use when a user wants to export their own macOS WeChat 4.2.0 chats, resume an offline export, or refresh a private knowledge folder while keeping existing contact groups, aliases, and numbered folders."
---

# 离线导出 macOS 微信 4.2.0 私聊

项目来源：[bmdy6/wechat-mac-export](https://github.com/bmdy6/wechat-mac-export)。先定位已克隆的项目并读 `README.md`；只有单独安装的 Skill 时，可先克隆项目，再按 README 在其 `.venv` 安装命令，不依赖全局 Python 已有依赖。

需要从头执行或交接这套流程时，读取 [完整更新流程](references/workflow.md)，其中包括输入检查、离线导出、保留结构写回、分组补充、完成验收及回滚。

本机已有 `~/.config/wechat-mac-export/profile.json` 时先读其中的路径配置。输入字段是 `repository`、`live_source`、`key_file`、`private_output`、`work_root`、`vm_path` 和 `vm_uuid`；完成后记录 `last_success`、`last_run_dir`、`last_backup`，方便下次续接或回滚。配置不存密钥值，目录 `700`、文件 `600`。没有配置时只补齐实际缺少的输入，已获授权的例行导出不重复询问。

在仓库外创建权限为 `700` 的本次工作目录。先检查版本、目标和已有密钥文件。已有密钥经当前快照校验通过后直接复用，不为测试虚拟机而重新登录微信。密钥只通过本地文件路径传入，聊天内容和联系人映射留在本地。

1. 需要用户补齐输入时运行 `wechat-mac-export handoff --action user --directory <工作目录>`，告诉用户提示文件的位置并等待。官方微信界面实际显示扫码要求且与当前任务相关时，才写 `--action scan` 的提示文件。扫码由本人核对并完成。
2. 有来源后运行 `snapshot --source <db_storage> --output <新快照目录>`。遇到源文件变化时，保留原目录并从一致的副本重新复制。
3. 运行 `verify --snapshot <快照目录> --key-file <已有密钥文件> --handoff-dir <工作目录>`。只有所有库通过首页 HMAC 才能继续。找不到密钥或 HMAC 不匹配就停在此处，请用户检查输入。
4. 运行 `export --snapshot <快照目录> --key-file <已有密钥文件> --output <新导出目录> --handoff-dir <工作目录>`。账号识别失败时让用户提供数据库内的本人账号标识，再加 `--self-id` 重试。
5. 读 `统计.md` 和导出清单的统计字段，核对实际编号目录、JSONL 行数和 Markdown 文件数。检查私有工作目录是否残留本次明文 SQLite 临时目录。汇报导出路径、会话和消息数、未识别会话表数及需要本人处理的事项。

## 更新已经整理过的私有知识目录

用户要求更新已有知识库时，不把这次按消息量排名的编号目录直接覆盖旧目录；旧编号通常已被人工分组、代号和备注引用。用 `conversation_id` 对应旧清单，保留旧目录名、代号和原备注，新联系人追加编号。

该模式只处理已有的原始私有聊天导出目录，不处理 `personal/`、`company/` 等正式知识文档；正式知识内容仍走知识库 MCP 的受保护写入。先确认私有目录被知识库 Git 忽略，不把本次聊天或路径配置放进公开仓库。

使用本 Skill 自带的 `scripts/refresh_knowledge.py`。`CURRENT` 是已有私有聊天目录，`EXPORT` 是上面刚完成校验的新导出，`PREPARED`、`BACKUP` 为互相独立且尚不存在的目录：

```sh
python3 "$SKILL_DIR/scripts/refresh_knowledge.py" prepare \
  --export "$EXPORT" --current "$CURRENT" --output "$PREPARED"
```

准备阶段核对全部历史文本与记录重复次数；旧媒体占位按时间和类型匹配。缺失历史时停止，不删除旧记录或猜测匹配。它完整保留人工文件，将 `人际分组.md` 的二级标题与编号列表派生为 `分组索引.md` / `.json`，并保留每项原行号和用户标注/推断依据；新联系人保持未分组。人工分组原文、旧分析素材和相关映射不重写；仅在原代号表末尾追加新增联系人。

核对新清单行数、旧编号和目录是否稳定，人工文件哈希是否保留，分组索引是否与原文一致。用户已授权更新后直接执行最后写回：

```sh
python3 "$SKILL_DIR/scripts/refresh_knowledge.py" apply \
  --prepared "$PREPARED" --current "$CURRENT" --backup "$BACKUP"
```

`apply` 检查原目录和暂存目录的版本指纹，再替换目录并保留完整旧目录；编辑器期间修改任何人工文件都会拒绝写回。写后回读原路径的清单、分组与统计，再确认 Git 忽略规则及无明文 SQLite 残留。汇报当前数据范围、新增消息/联系人、未分组联系人和回滚位置；旧分析素材保留原截止时间，不声称已经重新分析。

用户已要求根据聊天补充分组时，可以读取新增或未分组联系人的首尾、关系背景和必要的中间片段；已有本人明确标注优先。昵称、聊天量和称呼不能单独证明亲疏、亲属关系或雇佣关系。将“用户标”“聊天明确”“按备注/昵称”“AI 推，待确认”分开记录，附本地消息行号或其他已读知识来源；信息不足的保留“待确认”，不猜具体身份。同一人可能有多个账号时，保留账号编号，不自动合并。只查看抽样片段时如实说明覆盖范围，不称通读全部历史。

分组原文的补充单独先做差异和版本保护，再重新运行 prepare/apply 更新派生索引；脚本自身不调用模型、不推断关系。后续更新优先复用已有分类，只审阅新增或仍待确认的联系人。

## 权限或虚拟机接力

- `PermissionError` / `Operation not permitted` 表示读取权限不足，不表示账号目录不存在。可在用户授权的数据范围内通过 Finder 正常复制本次 `db_storage` 到私有工作目录，再对副本执行命令；不要把旧快照冒充当前数据。复制时保留账号上层目录，避免丢失本人账号识别依据。
- 无法直接核对源文件时，可从同一来源分别复制两次，比较相关 `.db` 和 `-wal` 的哈希，再继续 HMAC、逐页认证和 SQLite 检查。两份一致不能证明全目录原子快照；不一致时使用稳定备份，或由本人选择合适时机退出微信再复制。
- 只有已有密钥不可用时才进入现有虚拟机取钥接力。先检查现有虚拟机、来宾连接和官方微信状态；本技能不附带新的取钥程序。已有取钥流程得到的候选必须对当前消息库与联系人库通过 HMAC 校验后才能使用；需要本人登录或扫码时才发人工接力提示。不要用启动虚拟机代替取钥成功的证据。
- 本次启动或恢复过的虚拟机在结束后恢复原先运行/挂起状态；不改变宿主机 SIP，不为离线导出重签或改动宿主微信。

遇到现用微信正在运行、目标已存在、版本不同或用户仅授权比较时，按原任务边界继续处理。复制只读，导出需要新目录。终端、聊天、Git 和其他外部服务只保留用户授权的汇总信息。
