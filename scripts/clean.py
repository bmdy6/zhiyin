#!/usr/bin/env python3
"""Round 1 only: pinned public sources -> rules -> DeepSeek -> kept-only dedup."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SHA = "fd762df2e0ce4b8ec68b0087ba7178761045bd1a"
MODEL = "deepseek-flash"
EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
REPORT_SEED = 2026111201
REASONS = ("vulgar", "manipulative", "not_sendable", "duplicate", "broken")
SOURCES = {
    "evals_candidates_v1.json": (
        "evals/evals_candidates_v1.json",
        "2f97ed87b23c31879f32c453b8570b0228b804dfa4ccbff60b177b380c7ec7da",
    ),
    "examples-library.md": (
        "skill/references/examples-library.md",
        "9b5611146dc8ddbdf5cb5601e8f41d23b4fe4a423b2e7dc55f8065d8e40d7107",
    ),
    "LICENSE": ("LICENSE", "59b22840e87e0ae9cb4aa795da99e5d7701dc5823840c2796bcef8984d8bb6d9"),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(encoded(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def download_sources(data: Path):
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, (upstream, checksum) in SOURCES.items():
        path = raw / name
        if not path.exists():
            response = httpx.get(
                f"https://raw.githubusercontent.com/tomwong001/qingsheng-skill/{SHA}/{upstream}",
                timeout=60,
                follow_redirects=True,
            )
            response.raise_for_status()
            if digest(response.content) != checksum:
                raise ValueError(f"Source checksum mismatch: {name}")
            path.write_bytes(response.content)
        if digest(path.read_bytes()) != checksum:
            raise ValueError(f"Cached source checksum mismatch: {name}")


def unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and (text[0], text[-1]) in [("“", "”"), ('"', '"'), ("'", "'")]:
        return text[1:-1]
    return text


def scene_for(context: str) -> str:
    if re.search(r"线上|微信|短信|QQ|社交软件|发消息|发短信|邮件", context):
        return "online"
    if re.search(r"酒吧|街头|餐厅|电影院|楼下|当面|书城|广场|逛街|走路|聚餐", context):
        return "offline"
    return "unknown"


def base_row(source, source_id, stage, stage_name, context, her, reply, bad, goal, why):
    row = {
        "id": f"qs-{'c' if source == 'candidates' else 'e'}-{source_id}",
        "source": f"qingsheng/{source}",
        "source_id": source_id,
        "stage": stage,
        "stage_name": stage_name,
        "scene": scene_for(context),
        "context": context,
        "her": her,
        "reply": reply,
        "bad": bad,
        "goal": goal,
        "why": why,
        "tags": [],
    }
    # Parenthesized narration is not her speech. Preserve it as context instead.
    if re.fullmatch(r"[（(].*[）)]", her, re.DOTALL):
        row["context"] += "；她的状态：" + her
        row["her"] = ""
    return row


def section(text: str, heading: str) -> str:
    match = re.search(
        r"^### " + heading + r"[^\n]*\n(.*?)(?=^### |\Z)", text, re.MULTILINE | re.DOTALL
    )
    return match[1].strip() if match else ""


def parse_candidates(payload: dict) -> list[dict]:
    rows = []
    for item in payload["evals"]:
        prompt, expected = item["prompt"], item["expected_output"]
        stage = re.search(r"【当前阶段】阶段([1-7])\s*([^\n]+)", prompt)
        context = re.search(r"【情境】([^\n]+)", prompt)
        her = re.search(r"她刚发来：([^\n]+)", prompt)
        if not stage or not context:
            raise ValueError(f"Cannot parse candidate metadata: {item['id']}")
        bad = [
            unquote(x.strip())
            for x in re.findall(r"^\s*-\s*(.+)$", section(expected, "不好的选项"), re.MULTILINE)
        ]
        rows.append(
            base_row(
                "candidates",
                item["id"],
                int(stage[1]),
                stage[2].strip(),
                context[1],
                unquote(her[1]) if her else "",
                unquote(section(expected, "参考优秀答案")),
                bad,
                section(expected, "核心策略目标"),
                None,
            )
        )
    return rows


def parse_examples(text: str) -> list[dict]:
    rows = []
    stage, stage_name, unmapped = None, None, False
    for block in re.split(r"(?=^## |^\*\*场景\*\*)", text, flags=re.MULTILINE):
        if block.startswith("## "):
            heading = block.splitlines()[0][3:].strip()
            match = re.fullmatch(r"阶段([1-7])：(.+)", heading)
            if match:
                stage, stage_name, unmapped = int(match[1]), match[2], False
            elif heading == "挽回/冷激活":
                stage, stage_name, unmapped = 7, heading, True
            else:
                raise ValueError(f"Unexpected stage heading: {heading}")
        elif block.startswith("**场景**"):
            if stage is None:
                raise ValueError("Example before stage heading")
            match = re.fullmatch(
                r"\*\*场景\*\*：([^\n]+)\n她：([^\n]+)\n→ (.*?)\n为什么：(.*?)\n别这样：(.*)",
                block.strip(),
                re.DOTALL,
            )
            if not match:
                raise ValueError(f"Cannot parse example {len(rows) + 1}")
            context, her, reply, why, bad = match.groups()
            row = base_row(
                "examples",
                len(rows) + 1,
                stage,
                stage_name,
                context,
                her,
                unquote(reply),
                [bad.strip()],
                "",
                why.strip(),
            )
            if unmapped:
                row.update(review=True, review_note="上游无编号分类暂映射7，保留原名；待审阶段映射")
            rows.append(row)
    return rows


def strip_annotations(row: dict) -> dict:
    """Remove only short edge tone/action notes; keep the exact source for audit."""
    pair = re.compile(r"（([^（）()]*)）|\(([^（）()]*)\)")

    def annotation(match):
        original_note = next(g for g in match.groups() if g is not None)
        content = original_note.strip()
        if not 1 <= len(original_note) <= 15 or re.search(
            r"她(?:说|问|答)|对方(?:说|问|答)|确认.*后|等待|等(?:她|对方)|等.*(?:后|回应|反应)|随即|然后|随后|继续|方案|故事|如果|因为|我|你|[“”\"「」]",
            content,
        ):
            return None
        # Positive tone/action recognition: ordinary parenthetical speech is not metadata.
        pattern = (
            r"(?:幽默化解|语气.+|.+语气|轻松语气，不进不退|轻松(?:地|风趣地)?"
            r"|(?:附|配|配上|配合|送[一二三四五六七八九十\d]+个)?(?:笑|微笑|坏笑|苦笑|小坏笑|俏皮笑|轻松笑|偷笑|笑哭|笑脸|眨眼|惊讶|夸张惊讶|委屈|拥抱)(?:表情|笑意)?"
            r"|微笑(?:转身|扬左眉|转移话题)|淡定|突然严肃|轻声(?:温柔|细语)?"
            r"|指(?:着|了指|向).+|伸(?:出)?手(?:动作|示意)?|自然伸出手|手按.+|张开双臂"
            r"|轻触.+|捏脸|拿起她的手|平静地看着她|凝视\d+秒|走过时停下，转头"
            r"|(?:轻轻|自然|缓缓|同时|顺手|直接|突然|立刻)?(?:点头|摇头|转头|转身|回头|挑眉|停顿|起身|伸手|掏出|递上|放下|压低声音|把头转向|张开|抬手|举杯|示意|打回去).*)"
        )
        parts = re.split(r"[，,、]", content)
        if re.fullmatch(pattern, content) or (
            len(parts) > 1 and all(re.fullmatch(pattern, part.strip()) for part in parts)
        ):
            return original_note
        return None

    reply = row["reply"]
    remaining = reply.strip()
    prefix, suffix = [], []
    while match := pair.match(remaining):
        if (note := annotation(match)) is None:
            break
        prefix.append(note)
        remaining = remaining[match.end() :].lstrip()
    while remaining:
        matches = list(pair.finditer(remaining))
        if not matches or (match := matches[-1]).end() != len(remaining):
            break
        if (note := annotation(match)) is None:
            break
        suffix.insert(0, note)
        remaining = remaining[: match.start()].rstrip()
    if not remaining or not prefix + suffix:
        return row
    return dict(
        row, reply=remaining, original_reply=reply, stripped_annotation="\n".join(prefix + suffix)
    )


def rule_reason(row: dict) -> tuple[str, str] | None:
    reply = row["reply"]
    if not reply.strip():
        return "broken", "无法解析非空 reply"
    if re.search(r"下面已经竖|开房.*上床|上床.*开房|做爱|鸡巴|阴茎", reply):
        return "vulgar", "reply 命中明确性用语"
    if re.search(r"丑八怪|泼妇|你.*不配|你.*没人要", reply):
        return "manipulative", "reply 明确贬低人格或价值"
    if re.search(r"不.*(?:电话|号码)|不给|不留", row["her"]) and re.search(
        r"就留?这一次|下不为例", reply
    ):
        return "manipulative", "对方拒绝后仍以就这一次/下不为例推进，绕过拒绝意愿"
    strategy = (row.get("goal") or "") + "；" + (row.get("why") or "")
    for match in re.finditer(
        r"故意制造不安|打压自信|废物测试|制造[^。；]*失去感|假装不在乎"
        r"|拒绝成本[极很]高|不给对方[^。；]*拒绝[^。；]*空间"
        r"|(?:不是|而不是)接受拒绝|找不到继续拒绝的着力点"
        r"|无需征求同意|去掉[^。；]*说不[^。；]*空间|(?:能让|让).{0,8}体面接受"
        r"|故意制造(?:焦虑|不安)|贬低对方[^。；]*抬高自己",
        strategy,
    ):
        prefix = strategy[max(0, match.start() - 6) : match.start()]
        if not re.search(r"而非|不是|避免|不要|不应|不再|不去|不能", prefix):
            return "manipulative", "goal/why 明确宣扬操纵"
    if len(reply) > 60:
        return "not_sendable", f"reply {len(reply)} 字符 > 60"
    if re.search(
        r"[（(][^）)]+[）)]|主推话术|备选[（(:：]|伸出手说|突然停下说|继续坐着吃完|表现得像|保持平静地继续"
        r"|\[[^\]]*(?:附上|插入|填写|展览类型|自己(?:的)?(?:号码|电话)|你的(?:名字|号码))[^\]]*\]"
        r"|^(?:笑一下|微笑|点头|摇头|停顿|看着她).{0,20}(?:说|回复|回答)[:：]",
        reply,
    ):
        return "not_sendable", "reply 包含动作、策略注释或操作建议"
    return None


def validate_label(label: dict) -> dict:
    if not isinstance(label, dict):
        raise TypeError("Label must be an object")
    if label.get("reason") not in (None, "vulgar", "manipulative", "not_sendable", "broken"):
        raise ValueError("Invalid reason")
    if type(label.get("review")) is not bool:
        raise ValueError("Invalid review")
    if type(label.get("confidence")) not in (float, int) or not 0 <= label["confidence"] <= 1:
        raise ValueError("Invalid confidence")
    if label.get("scene") not in ("online", "offline", "unknown"):
        raise ValueError("Invalid scene")
    tags = label.get("tags")
    if (
        not isinstance(tags, list)
        or not 1 <= len(tags) <= 4
        or not all(isinstance(t, str) and 0 < len(t) <= 20 for t in tags)
    ):
        raise ValueError("Invalid tags")
    if not isinstance(label.get("evidence"), str) or not label["evidence"].strip():
        raise ValueError("Missing evidence")
    if "reason" not in label:
        raise ValueError("Missing reason")
    return label


def apply_label(row: dict, label: dict) -> dict:
    result = dict(row, tags=label["tags"], scene=label["scene"])
    rule = rule_reason(row)
    if rule:
        result.update(reason=rule[0], decision_by="rule", decision_note=rule[1])
    elif label["review"] or label["confidence"] < 0.9:
        result.update(review=True, review_note=label["evidence"])
    elif label["reason"]:
        result.update(reason=label["reason"], decision_by="llm", decision_note=label["evidence"])
    uncertain_reply = re.search(
        r"阿笨|像你失散的表亲|乖女孩|陈冠希|冠希哥|玩.?玻璃|淡出鸟", row["reply"]
    )
    uncertain_goal = re.search(
        r"语义双关|降低[^。；]*警觉|不激活[^。；]*理性|荡妇防御|冷读[^。；]*地址|出于关心的强势"
        r"|逼对方回复|两难选项|反将[^。；]*幼稚",
        row.get("goal") or "",
    )
    if "reason" not in result and (uncertain_reply or uncertain_goal):
        note = "称呼、隐喻或互动意图依赖上下文，不能确认边界，保留待审"
        result.update(
            review=True, review_note=(result.get("review_note", "") + "；" + note).lstrip("；")
        )
    return result


def label_key(row: dict, prompt: str) -> str:
    return digest((MODEL + "|thinking=disabled|" + prompt + encoded(row)).encode())


def read_key() -> str:
    vault = json.loads(Path.home().joinpath(".config/ai-vault/vault.json").read_text())
    # Explicit personal provider only; never fall back to company credentials.
    value = vault["model-providers"]["deepseek"]["DEEPSEEK_API_KEY"]["value"]
    if not isinstance(value, str) or not value:
        raise ValueError("Personal DeepSeek key unavailable")
    return value


def classify(row: dict, prompt: str, cache: Path, offline: bool) -> dict:
    key = label_key(row, prompt)
    path = cache / f"{key}.json"
    if path.exists():
        entry = json.loads(path.read_text())
        if (
            entry.get("id") != row["id"]
            or entry.get("input_sha256") != key
            or entry.get("model") != MODEL
            or entry.get("thinking") != "disabled"
        ):
            raise ValueError("Label cache identity mismatch")
        validate_label(entry["label"])
        return entry
    if offline:
        raise ValueError(f"Missing label cache: {row['id']}")
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": "Bearer " + read_key()},
                    json={
                        "model": MODEL,
                        "thinking": {"type": "disabled"},
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "max_tokens": 400,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": encoded(row)},
                        ],
                    },
                )
                response.raise_for_status()
                body = response.json()
            choice = body["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("Truncated model output")
            if choice["message"].get("reasoning_content"):
                raise ValueError("Thinking unexpectedly enabled")
            label = validate_label(json.loads(choice["message"]["content"]))
            entry = {
                "id": row["id"],
                "input_sha256": key,
                "model": MODEL,
                "thinking": "disabled",
                "served_model": body.get("model"),
                "usage": body.get("usage"),
                "label": label,
            }
            cache.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded(entry) + "\n", encoding="utf-8")
            return entry
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as error:
            # Log only exception class, never headers, response bodies, or credentials.
            error_type = type(error).__name__
            if attempt < 2:
                time.sleep(2**attempt)
    return {
        "id": row["id"],
        "input_sha256": key,
        "model": MODEL,
        "thinking": "disabled",
        "error": error_type,
        "label": {
            "tags": ["待审核"],
            "scene": row["scene"],
            "reason": None,
            "review": True,
            "confidence": 0,
            "evidence": f"API/标签校验重试耗尽：{error_type}",
        },
    }


def deduplicate(rows: list[dict], vectors: np.ndarray) -> tuple[list[dict], list[dict]]:
    if not rows:
        return [], []
    vectors = np.asarray(vectors, dtype=np.float64)
    if vectors.shape[0] != len(rows) or not np.isfinite(vectors).all():
        raise ValueError("Invalid embeddings")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Zero embeddings")
    vectors = vectors / norms
    indices, kept, dropped = [], [], []
    for i, row in enumerate(rows):
        scores = vectors[indices] @ vectors[i] if indices else np.array([])
        if len(scores) and float(scores.max()) > 0.92:
            j = int(scores.argmax())
            dropped.append(
                dict(
                    row,
                    reason="duplicate",
                    duplicate_of=rows[indices[j]]["id"],
                    similarity=float(scores[j]),
                    decision_by="vector",
                    decision_note="her + reply 余弦相似度 > 0.92；保留此前已保留条目",
                )
            )
        else:
            indices.append(i)
            kept.append(row)
    return kept, dropped


def embeddings(rows: list[dict], data: Path, offline: bool):
    texts = [r["her"] + "\n" + r["reply"] for r in rows]
    key = digest((EMBED_MODEL + encoded(texts)).encode())
    path = data / "cache" / f"embeddings-{key}.npy"
    if path.exists():
        return np.load(path, allow_pickle=False)
    if offline:
        raise ValueError("Missing embeddings cache")
    from fastembed import TextEmbedding

    model = TextEmbedding(
        model_name=EMBED_MODEL, cache_dir=str(data / "cache" / "models"), threads=2
    )
    matrix = np.array(list(model.embed(texts, batch_size=64)), dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, matrix, allow_pickle=False)
    return matrix


def pilot_rows(rows: list[dict]) -> list[dict]:
    rng = random.Random(20261112)
    groups = {}
    for row in rows:
        groups.setdefault((row["source"], row["stage"], row["stage_name"]), []).append(row)
    picked = [rng.choice(group) for group in groups.values()]
    # Cover each original stage name plus the four PR review regressions (20 total).
    regression_ids = {"qs-c-3388", "qs-c-2545", "qs-c-2781", "qs-c-2418"}
    picked.extend(r for r in rows if r["id"] in regression_ids and r not in picked)
    used = {r["id"] for r in picked}
    picked.extend(rng.sample([r for r in rows if r["id"] not in used], 20 - len(picked)))
    return picked


def report(rows, kept, dropped, audits, data):
    lines = [
        "# 第 1 轮：样本清洗报告",
        "",
        f"上游固定提交：`{SHA}`；输入 1395 + 42 = {len(rows)} 条。",
        "",
        "## 各阶段统计",
        "",
        "| 阶段 | 原名 | 原数 | 保留 | vulgar | manipulative | not_sendable | duplicate | broken | review（保留中） |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage, name in sorted({(r["stage"], r["stage_name"]) for r in rows}):
        raw = [r for r in rows if (r["stage"], r["stage_name"]) == (stage, name)]
        k = [r for r in kept if (r["stage"], r["stage_name"]) == (stage, name)]
        d = Counter(r["reason"] for r in dropped if (r["stage"], r["stage_name"]) == (stage, name))
        lines.append(
            f"| {stage} | {name} | {len(raw)} | {len(k)} | "
            + " | ".join(str(d[x]) for x in REASONS)
            + f" | {sum(bool(r.get('review')) for r in k)} |"
        )
    counts = Counter(r["reason"] for r in dropped)
    lines.append(
        f"| **合计** | | **{len(rows)}** | **{len(kept)}** | "
        + " | ".join(f"**{counts[x]}**" for x in REASONS)
        + f" | **{sum(bool(r.get('review')) for r in kept)}** |"
    )
    rules = sum(rule_reason(r) is not None for r in rows)
    llm = sum(r.get("decision_by") == "llm" for r in dropped)
    tokens = Counter()
    for audit in audits:
        usage = audit.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            tokens[key] += usage.get(key, 0)
    lines.extend(
        [
            "",
            "## 方法与边界",
            "",
            f"- 规则后 {len(rows) - rules} 条（规则删 {rules}）；LLM 后 {len(rows) - rules - llm} 条（LLM 新增删 {llm}）；向量去重后 {len(kept)} 条（重复删 {counts['duplicate']}）。",
            f"- 全部 {len(audits)} 条均请求/复用 `{MODEL}` 标签，`thinking.type=disabled`；失败降级 {sum('error' in a for a in audits)} 条。",
            f"- 本轮有效响应 token：{encoded(dict(tokens))}；不含无效响应/重试。",
            "- 提示词见 `prompts/label.txt`，逐条标签与输入摘要见 `labels.jsonl`。低置信/不确定及称呼、隐喻、互动意图边界不明保留 review（详见 apply_label）；明确规则优先。bad 仅作反例，不用来触发删除。",
            "- stage 保留上游数字及原名，两个阶段5不合并名称；示例库“挽回/冷激活”没有数字，暂映射7并 review。",
            "- 无台词/纯括号状态写入 context，her 为空。示例的 goal 上游未提供，存空字符串，why 保留完整解释。",
            f"- 去重用 `{EMBED_MODEL}`（fastembed，CPU）对 `her + 换行 + reply` 编码，L2 归一化，余弦严格 >0.92；按候选源后示例源顺序只比较已保留项，不通过已删除项传递。",
            f"- 返工1报告使用新随机种子 {REPORT_SEED}（前版20261112），从最终保留/删除集合各独立抽30条，贴全部字段。重复项有 duplicate_of 和 similarity 可回查。",
            f"- 首尾≤15字的纯语气/表情/动作标注共剥离 {sum('stripped_annotation' in r for r in rows)} 条；仅去除标注，剩余台词重新过规则和LLM。original_reply保留精确原文，stripped_annotation记录原括号内容（多项以换行分隔）；中间括号、多轮/条件剧本及长标注不剥离。",
            "- PR首轮Claude抽查约5处误删/漏删，未通过≤3标准。本报告是返工后新样本，尚待Claude重新抽查。",
            "- broken 计数为0不表示人工质量验收通过。Claude 尚需独立抽查以下60条，误删或漏删 ≤3才满足 Issue 人工验收。",
            "",
            "## 原文件 SHA-256（缓存不提交）",
            "",
        ]
    )
    lines += [f"- `{name}`: `{checksum}`" for name, (_, checksum) in SOURCES.items()]
    for title, collection in [("随机30条保留", kept), ("随机30条删除", dropped)]:
        lines += ["", f"## {title}", ""]
        for row in random.Random(REPORT_SEED).sample(collection, min(30, len(collection))):
            lines += [
                f"### {row['id']}",
                "",
                "```json",
                json.dumps(row, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    (data / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def validate_pilot(data: Path, prompt: str):
    approval = json.loads((data / "pilot-approved.json").read_text())
    if (
        approval.get("prompt_sha256") != digest(prompt.encode())
        or approval.get("pilot_sha256") != digest((data / "pilot.jsonl").read_bytes())
        or approval.get("count") != 20
        or approval.get("status") != "reviewed-before-full"
    ):
        raise ValueError("Pilot review missing or stale")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true")
    mode.add_argument("--full", action="store_true")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Require existing raw/label/vector caches; no network",
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    data = ROOT / "data"
    if args.offline and any(not (data / "raw" / name).exists() for name in SOURCES):
        raise ValueError("Missing raw cache in offline mode")
    download_sources(data)
    rows = parse_candidates(json.loads((data / "raw/evals_candidates_v1.json").read_text()))
    examples = parse_examples((data / "raw/examples-library.md").read_text())
    if len(rows) != 1395 or len(examples) != 42:
        raise ValueError("Unexpected source counts")
    rows = [strip_annotations(row) for row in rows + examples]
    prompt = (ROOT / "prompts/label.txt").read_text()
    if args.full:
        validate_pilot(data, prompt)
    selected = pilot_rows(rows) if args.pilot else rows
    rule_counts = Counter(rule_reason(r)[0] for r in selected if rule_reason(r))
    print(
        f"Rule pass {sum(rule_counts.values())}/{len(selected)}: {encoded(dict(rule_counts))}",
        flush=True,
    )
    audits = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 16))) as pool:
        futures = {
            pool.submit(classify, row, prompt, data / "cache/labels", args.offline): row
            for row in selected
        }
        for future in as_completed(futures):
            row = futures[future]
            audits[row["id"]] = future.result()
            if len(audits) % 50 == 0 or len(audits) == len(selected):
                print(f"Labels {len(audits)}/{len(selected)}", flush=True)
    results = [apply_label(r, audits[r["id"]]["label"]) for r in selected]
    if args.pilot:
        write_jsonl(
            data / "pilot.jsonl",
            [
                {"sample": r, "audit": audits[r["id"]], "result": result}
                for r, result in zip(selected, results)
            ],
        )
        print("Pilot ready: review data/pilot.jsonl before writing pilot-approved.json")
        return
    dropped = [r for r in results if "reason" in r]
    candidates = [r for r in results if "reason" not in r]
    kept, duplicates = deduplicate(candidates, embeddings(candidates, data, args.offline))
    dropped += duplicates
    order = {r["id"]: i for i, r in enumerate(rows)}
    dropped.sort(key=lambda r: order[r["id"]])
    if len(kept) + len(dropped) != 1437 or not all(
        r["reply"] and 1 <= r["stage"] <= 7 for r in kept
    ):
        raise ValueError("Final invariant failed")
    write_jsonl(data / "samples.jsonl", kept)
    write_jsonl(data / "samples.dropped.jsonl", dropped)
    write_jsonl(data / "labels.jsonl", [audits[r["id"]] for r in rows])
    report(rows, kept, dropped, [audits[r["id"]] for r in rows], data)
    license_text = (data / "raw/LICENSE").read_text()
    (data / "ATTRIBUTION.md").write_text(
        f"# Attribution\n\n来源：[tomwong001/qingsheng-skill](https://github.com/tomwong001/qingsheng-skill/tree/{SHA})\n\n固定提交：`{SHA}`。仅使用 evals/evals_candidates_v1.json 和 skill/references/examples-library.md；未使用 chatlogs。\n\n样本/报告摘录为上游作品的解析与筛选；仅剥离首尾≤15字的纯标注，以original_reply与stripped_annotation保留改动依据；其他台词不改写。标签、删除依据由本项目生成。上游 MIT 许可全文如下：\n\n```text\n{license_text}```\n",
        encoding="utf-8",
    )
    print(
        encoded(
            {
                "parsed": len(rows),
                "kept": len(kept),
                "dropped": len(dropped),
                "reasons": dict(Counter(r["reason"] for r in dropped)),
            }
        )
    )


if __name__ == "__main__":
    main()
