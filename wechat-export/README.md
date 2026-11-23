# macOS 微信聊天导出与知识库更新

把 macOS 微信 **4.2.0** 的加密数据库副本转成私聊 JSONL、Markdown 和统计表，也可以更新已经整理过的私有知识目录，保留联系人分组、代号、原目录及人工备注。

运行时需要账号对应的**已有授权数据库主密钥**。复制、校验和导出在本地完成，微信和 macOS 系统保护无需调整。项目不包含自动取钥程序。

## 从哪里开始

- **首次导出**：按下方第 1–4 步准备输入、安装、复制校验并导出。
- **更新已有知识库**：完成本次导出后，执行第 5 步，沿用历史编号和分组。
- **交给 Agent 执行**：读取 [Agent 技能](skills/wechat-mac-export/SKILL.md)；从准备到验收、异常接力和回滚，见 [完整更新流程](skills/wechat-mac-export/references/workflow.md)。

常用请求：**“按现有分组更新微信聊天知识库。”** 用户已授权依据聊天补充分组时，Agent 可检查新增及待确认联系人，并保留判断依据。

## 适用范围

| 项目 | 范围 |
| --- | --- |
| 微信 | macOS 4.2.0 的 SQLCipher 4 页面格式 |
| 验证环境 | Apple 芯片 Mac，macOS 27.0.1，Python 3.13 |
| Python | 3.11 及以上 |
| 导出内容 | 普通消息分库中的私聊，文本及引用回复正文，其他类型以占位符保留 |
| 知识库更新 | 按会话标识保留旧编号、代号和目录，新联系人追加编号；更新清单、统计及分组索引 |
| 分组 | 脚本读取既有分组及来源标记；聊天判断由获授权的 Agent 或本人完成 |
| 其他数据 | 群聊、公众号、业务分库和附件文件由使用者另行保留或导出 |

## 1. 人工准备

- 确认正在处理自己的账号和数据，准备一个账号的 `db_storage` 目录。它在微信容器的 `Data/Documents/xwechat_files/<账号目录>/` 下。已有完整副本时可以直接选副本。
- 准备和该副本对应的已有授权密钥文件。格式是 32 字节二进制，或 64 个十六进制字符。密钥文件放在 Git 仓库之外，推荐权限为 `600`。
- 如官方微信界面要求身份验证，由本人核对设备后手动扫码。导出流程只用文件，已有输入时可以直接开始复制。
- 可用单独的用户账户或虚拟机做环境隔离，避免另一个微信安装误开日常使用的数据库。离线导出的自动步骤可直接在 Mac 的普通终端运行，保持 SIP 开启。

## 2. 安装命令

首次使用先克隆项目：

```sh
git clone https://github.com/bmdy6/wechat-mac-export.git
cd wechat-mac-export
```

在仓库根目录安装。环境及依赖准备好后，复制、验证和导出全程离线。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
```

## 3. 复制并校验加密快照

下面的路径是占位示例。`SOURCE` 选到单个账号的 `db_storage`，`KEY_FILE` 指向已有授权密钥，`WORK` 放在仓库之外。

```sh
SOURCE="/path/to/account/db_storage"
KEY_FILE="/path/to/local/master.key"
WORK="$HOME/wechat-export-work"
mkdir -m 700 -p "$WORK"

.venv/bin/wechat-mac-export snapshot --source "$SOURCE" --output "$WORK/snapshot"
.venv/bin/wechat-mac-export verify --snapshot "$WORK/snapshot" --key-file "$KEY_FILE" --handoff-dir "$WORK"
```

`snapshot` 以只读模式复制普通消息分库、可用的联系人和会话元数据库，以及各自的 `-wal`、`-shm`。它比较复制前源文件、复制后源文件和副本的 SHA-256。需要完整账号和附件备份时，另行保存完整备份。

`verify` 为每个库使用该库首页的盐和已有主密钥派生页面密钥，再用 HMAC 验证首页。密钥缺失或 HMAC 不匹配时，命令以非零状态退出。指定 `--handoff-dir` 后，会在该目录写 `需要用户.txt`。

## 4. 离线解密副本并导出

```sh
.venv/bin/wechat-mac-export export --snapshot "$WORK/snapshot" --key-file "$KEY_FILE" \
  --output "$WORK/export" --handoff-dir "$WORK"
```

导出时逐页校验 HMAC，恢复 WAL 中完整提交的事务，并对每个解密库运行 SQLite `quick_check`。明文 SQLite 仅存在于目标目录旁的临时目录，命令在正常退出及可处理的错误退出时清理。强制杀进程或断电后，可在私有工作目录检查残留的 `.export-*`。

每个编号目录包含 `chat.jsonl` 和 `chat.md`。根目录的 `导出清单.json` 记录编号、会话标识和条数，`统计.md` 记录起止时间和未识别会话表数。JSONL 保留 UTC 时间、消息类型、发送方、`server_id`、`local_id` 和分库名。客户端只分配本地 ID 时，应把分库名和会话一起纳入比较。

## 人工接力文件

需要交给本人处理时，可以从终端或 Agent 写提示文件。已有提示文件会保留。

```sh
.venv/bin/wechat-mac-export handoff --directory "$WORK" --action user
.venv/bin/wechat-mac-export handoff --directory "$WORK" --action scan
```

第二条只生成 `需要扫码.txt`。只有官方界面实际要求扫码时，才需要执行这项人工操作。本人处理后重新运行相应命令。

## 常见问题

- **首页 HMAC 不匹配**。确认密钥对应的是这份快照、账号和微信版本，确认密钥文件格式。若全库都失败，优先核对这几项。首页 HMAC 通过后仍要完成逐页验证和 SQLite 检查。
- **复制期间源库变化**。使用稳定的备份重试，或由本人在合适的时机退出微信后重新复制。正在写入时，单独复制 `.db` 容易漏掉 WAL 里已提交的消息。
- **合盖后进程中断**。运行复制和导出时保持电脑唤醒。被中断后检查临时目录，用新的输出目录重新运行。
- **旧版微信打开过数据库**。客户端可能自动写入或迁移数据。把旧版安装和测试数据放在独立环境，保留原备份以便核对。
- **无法确认本人账号**。检查快照目录来源，或给 `export` 加 `--self-id`，使用数据库内对应账号的原始标识。
- **统计里出现未识别会话表**。当前导出只含身份可识别的私聊。保留加密快照，并检查联系人和会话数据库是否齐全。

只处理自己的数据。密钥、快照、导出清单和聊天文件应保存在私有目录。机器上的分享、同步和备份设置也会影响这些文件的可见范围。

## 5. 更新已有的私有知识目录

已有联系人分组、代号或带备注的编号目录时，使用 [结构化更新脚本](skills/wechat-mac-export/scripts/refresh_knowledge.py)。它按会话标识复用历史编号与目录，新联系人追加编号；人工分组原文保持不变，同时生成可阅读和可解析的分组索引。历史分析素材保留原时间范围。

JSONL 更新为完整、按时间排列的记录；已有 Markdown 保留原文与人工备注，只在末尾增补本批次新增消息，回补旧日期的消息也出现在相应增补批次中。统计、导出清单和分组索引按当前数据重新生成。

先完成上面的离线导出，再准备新目录。下面的路径均为占位示例，暂存目录和备份目录必须尚不存在，且与现有目录互相独立：

```sh
python3 skills/wechat-mac-export/scripts/refresh_knowledge.py prepare \
  --export "/path/to/new/export" --current "/path/to/private/chats" \
  --output "/path/to/work/prepared"
```

核对摘要、分组和旧文件保留情况后写回。原目录或暂存目录在准备后有变化时，命令拒绝覆盖；写回保留完整旧目录以便回滚。

```sh
python3 skills/wechat-mac-export/scripts/refresh_knowledge.py apply \
  --prepared "/path/to/work/prepared" --current "/path/to/private/chats" \
  --backup "/path/to/work/previous-chats"
```

只将此模式用于原始私有聊天导出。它不替代正式知识文档的 MCP 写入流程。路径配置、分组、联系人映射及输出数据应留在本地，不提交到本仓库。

### 更新后的目录

```text
私有聊天目录/
├── 001-原目录/
│   ├── chat.jsonl          # 完整时间序记录
│   └── chat.md             # 原文及备注保留，末尾增补新消息
├── 002/                   # 新联系人追加编号
├── 人际分组.md            # 本人维护或授权 Agent 补充的分组原文
├── 分组索引.md            # 按分组阅读聊天的入口
├── 分组索引.json          # 分组、原文行号及确认状态
├── 导出清单.json          # 固定编号、会话标识及统计
├── 代号映射.md            # 保留原文，末尾追加新联系人
├── 统计总表.md
├── 统计口径.md
└── 更新说明.md
```

`人际分组.md` 使用二级标题和编号列表，例如 `## 同学` 下的 `- 001 联系人甲：共同课程——聊天明确`。脚本分别记录“用户标”“聊天明确”“按备注/昵称”和 AI 推断；AI 推断保留待确认状态。新联系人先获得稳定编号，再按授权补充分组并重新生成索引，具体顺序见 [完整更新流程](skills/wechat-mac-export/references/workflow.md#五按授权补充分组)。

旧版 JSONL 缺少数据库行 ID 时，历史覆盖校验以时间、发送方、类型、文本和重复次数为依据；媒体占位忽略不同导出器的占位文案差异，不能据此证明数据库行身份。

## Agent 与开发验证

Claude Code 或 Codex 可读取仓库里的 [Agent 技能](skills/wechat-mac-export/SKILL.md)。独立安装技能时，技能脚本目录与提供 CLI 的项目目录可以不同；本机路径配置方式见技能说明。

单元测试只使用合成数据，覆盖离线解密、WAL 恢复、历史记录保护、分组与编号稳定、人工备注保留、版本冲突和备份等行为。

```sh
.venv/bin/python -m unittest discover -s tests -v
```

WAL 验证代码源自 [chatlog-keeper](https://github.com/labazhou2024/chatlog-keeper)，按 [MIT 许可](LICENSE) 分发。
