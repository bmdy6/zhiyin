# Agent context implementation plan

> **For agentic workers:** Execute sequentially with executing-plans. Git delivery is authorized; do not merge.

**Goal:** Implement Issue #10 as offline, read-only context tools with private evidence references.

**Architecture:** Keep the existing SQLite import and six-hour reply pairs. Import source-line and export-group metadata plus private aliases into local-only tables. A shared context reader opens the database read-only, applies group exclusion and redaction, computes statistics and rule candidates, and composes reply context using read-only historical retrieval.

**Tech Stack:** Python, SQLite, FastMCP, pytest; existing dependencies only.

## Global constraints

- Source exports are read-only. Private text, identities, and alias maps never enter Git, logs, PRs, or reports.
- All added tools never call model APIs or network services and never mutate local state.
- Default identity is a code; revealing names requires explicit `reveal_names=true`.
- Exclude merchant/service chats by default. Rule matches are candidates, not diagnoses.
- Old API drafting and relationship mutation require explicit configuration; default MCP is read-only.
- Push `agent-context`, open a PR closing #10, leave merging to Claude.

## Behavior and evidence matrix

| Path | Expected result | Evidence |
|---|---|---|
| Missing/empty local DB | Empty bounded result, no file creation | synthetic test |
| Default identity | Codes and masked contact details, no IDs/labels | synthetic privacy test |
| Explicit reveal | Labels available; contact details remain masked | synthetic test |
| Merchant/excluded/group chat | Absent from default results and retrieval | synthetic test |
| Exactly six hours / same second | 新段落与原始导出顺序 | synthetic test |
| Each tool and candidate kind | Source line references, bounded windows | synthetic and real aggregates |
| Invalid input/failure | 固定脱敏错误，不包含私密值 | protocol test |
| Blocked network | All new tools work; zero attempted connections | child-process test |
| Default registration | No draft API or write tool | stdio listing |

## Tasks

- [x] Extend import metadata without breaking existing import IDs or exclusions; preserve source line numbers.
- [x] Test and implement context statistics, list/search/window, five candidate kinds and privacy.
- [x] Add read-only reply retrieval and reply_context, editable problem list setup, MCP registration gates.
- [x] Extend offline and stdio coverage; document synthetic Agent usage and statistic definitions.
- [x] Run full tests, lint, formatting, build; validate real data with aggregate-only output and source integrity check.
- [x] Register local Codex MCP; execute the requested question with privacy-preserving tool-call evidence.
- [x] Review diff and privacy scan; prepare the report and PR text. Commit/push/open PR and archive DONE after this delivery gate.

## Conservative choices

Active days means a contact had a message within the requested number of days before the latest visible export message. Recent 90-day volume uses the same stable anchor. All nonsystem messages count for run/segment timing; lengths/questions use readable text. Missing response samples yield null. Session endings are structural observations, not evidence of rejection. Historical similarity uses local lexical retrieval without embedding cache writes.
