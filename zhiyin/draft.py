"""Shared analyze, decide, retrieve, write and review flow for reply drafts."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from zhiyin.contacts import ContactStore, validate_identity
from zhiyin.draft_models import Analysis, Candidate, Decision, Review
from zhiyin.resources import resource
from zhiyin.safety import draft_errors, handoff_reason, parse_chat, profile_echo, review_categories
from zhiyin.wechat_draft import (
    check_candidate,
    check_rules,
    immediate_handoff,
    load_context,
    rationale,
    source_rows,
)


@dataclass
class DraftResult:
    handoff: bool = False
    reason: str = ""
    messages: list[str] = field(default_factory=list)
    wait_minutes: int = 0
    analysis: Analysis | None = None
    notices: list[str] = field(default_factory=list)
    sample_ids: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    attempts: int = 0
    drafts: list[dict] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"[提示] {notice}" for notice in self.notices]
        if self.handoff:
            lines.append("[交给你] " + self.reason)
        elif self.drafts:
            for draft in self.drafts:
                lines.extend([f"[草稿] {draft['text']}", f"[理由] {draft['reason']}"])
        elif not self.messages:
            lines.extend(["[先不回]", "[理由] " + self.reason])
        else:
            lines.append(f"[等待 {self.wait_minutes}分钟]")
            lines.extend("[发送] " + text for text in self.messages)
            lines.append("[理由] " + self.reason)
        return "\n".join(lines)

    def as_tool_result(self) -> dict:
        return {
            "status": "草稿" if self.drafts else "这条你自己回",
            "drafts": self.drafts,
            "reason": self.reason,
            "notices": self.notices,
        }


def validate_goal(goal: str | None) -> None:
    if goal is not None and (not goal.strip() or len(goal) > 500):
        raise ValueError("goal须为1–500字符")


class DraftEngine:
    def __init__(
        self, model, samples, store: ContactStore | None = None, *, replies=None, style_path=None
    ):
        self.model, self.samples, self.store, self.replies = model, samples, store, replies
        self.style_path = (
            style_path if style_path is not None else Path.home() / ".style-polish/spoken.md"
        )

    async def run(
        self, platform: str, contact: str, text: str, *, goal: str | None = None
    ) -> DraftResult:
        """Keep the round-3 ledger behavior for its existing offline evaluation."""
        validate_identity(platform, contact)
        chat = parse_chat(text)
        validate_goal(goal)
        result = DraftResult()
        try:
            context = self.store.context(platform, contact)
        except Exception:  # noqa: BLE001 -- never print contact storage errors
            result.handoff, result.reason = True, "读取联系人失败，无法可靠生成草稿"
        else:
            direct = handoff_reason(chat[-1]["text"]) or handoff_reason(goal or "")
            if direct:
                result.handoff, result.reason = True, direct
            else:
                await self._compose(result, chat, context, goal)
        try:
            summary = (
                result.analysis.summary
                if result.analysis
                else "本次输入需本人查看，未生成可发送草稿"
            )
            self.store.update(
                platform, contact, summary, result.analysis.stage if result.analysis else None
            )
        except Exception:  # noqa: BLE001 -- never print contact storage errors
            result.notices.append("联系人记录更新失败，请检查本地存储")
        return result

    async def run_wechat(
        self, contact: str, message: str, *, goal: str | None = None, history=None
    ) -> DraftResult:
        if not contact.strip() or len(contact) > 200:
            raise ValueError("联系人须为1–200字符")
        if not message.strip() or len(message) > 10000:
            raise ValueError("消息须为1–10000字符")
        validate_goal(goal)
        if direct := immediate_handoff(message, goal):
            return DraftResult(handoff=True, reason=direct)
        try:
            private = await asyncio.to_thread(load_context, self.replies.store, contact)
        except Exception:  # noqa: BLE001 -- never print contact storage errors
            return DraftResult(handoff=True, reason="读取微信近况失败")
        if private is None:
            return DraftResult(handoff=True, reason="没有可用的联系人记录，请确认会话代号或备注")
        chat = [*(history or []), {"speaker": "对方", "text": message}]
        result = DraftResult()
        await self._compose(
            result, chat, private.model_context(), goal, contact=contact, tone=private.tone
        )
        return result

    def _style(self, result: DraftResult) -> str:
        if self.style_path.is_file():
            with self.style_path.open(encoding="utf-8") as handle:
                style = handle.read(100001)
            if not style.strip() or len(style) > 100000:
                raise ValueError("文风档案为空或过长")
            return style
        result.notices.append("未找到文风档案，使用 profiles/example.md")
        return resource("profiles/example.md")

    async def _compose(self, result, chat, context, goal, *, contact=None, tone=""):
        wechat = contact is not None
        payload = {"chat": chat, "contact_context": context, "goal": goal}
        if wechat:
            payload["mode"] = "wechat"
        current_step = "分析"
        try:
            analysis = Analysis.model_validate(await self.model.complete("analyze", payload))
            result.analysis = analysis
            payload["analysis"] = analysis.model_dump()
            if len(analysis.unknowns) >= 3:
                result.handoff, result.reason = True, "关键信息不足，分析有至少3项未知"
                return
            current_step = "决策"
            decision = Decision.model_validate(await self.model.complete("decide", payload))
            payload["decision"] = decision.model_dump()
            if decision.action in {"交给我", "先不回"}:
                result.handoff = wechat or decision.action == "交给我"
                if wechat:
                    result.reason = (
                        "由本人判断更稳妥"
                        if decision.action == "交给我"
                        else "当前对话可以自然结束"
                    )
                else:
                    result.reason = decision.reason
                return
            current_step = "检索"
            message = chat[-1]["text"]
            rows = []
            if wechat:
                rows = await asyncio.to_thread(self.replies.search, message, contact, 5)
            personal = bool(rows)
            if not rows:
                rows = await self.samples.search(
                    message,
                    stage=None if wechat or analysis.stage == "friend" else analysis.stage,
                    k=5,
                )
            if not rows:
                result.handoff, result.reason = True, "没有找到可参考的回复"
                return
            result.sample_ids = [row["id"] for row in rows]
            references = {}
            if wechat:
                payload["samples"], references = source_rows(rows, personal=personal)
                payload["wechat_tone"] = tone
                if not tone:
                    result.notices.append("当前口吻档案缺失或已过期，可重新生成")
            else:
                payload["samples"] = rows
            current_step = "读取文风"
            style = self._style(result)
            payload["spoken_style"] = style
            if not personal:
                payload["principles_source"] = resource("vendor/tong-jincheng/SKILL.md")
            for _ in range(2):
                result.attempts += 1
                current_step = "生成"
                try:
                    candidate = Candidate.model_validate(
                        await self.model.complete("write", payload)
                    )
                    if wechat:
                        errors = check_candidate(candidate, chat, context, rows, references, style)
                    else:
                        errors = draft_errors(candidate.messages, rows)
                        if profile_echo("".join([*candidate.messages, candidate.reason]), style):
                            errors.append("草稿疑似复述私密文风档案")
                except ValidationError:
                    errors = ["草稿JSON字段不符合契约"]
                    candidate = None
                feedback = errors
                if not errors:
                    current_step = "自检"
                    review = Review.model_validate(
                        await self.model.complete(
                            "check",
                            dict(payload, candidate=candidate.model_dump(exclude_none=True)),
                        )
                    )
                    if review.handoff:
                        result.handoff, result.reason = True, "自检发现需要本人处理的风险"
                        return
                    errors = check_rules(review) if wechat else []
                    if not review.passed or review.issues:
                        errors.append(
                            "模型语义自检未通过：" + "、".join(review_categories(review.issues))
                        )
                    feedback = [*errors, *review.issues]
                if not errors:
                    result.messages = candidate.messages
                    if wechat:
                        result.drafts = [
                            {
                                "text": text,
                                "reason": rationale(references[note.reference], note.rules),
                                "reference": references[note.reference],
                                "rules": note.rules,
                            }
                            for text, note in zip(
                                candidate.messages, candidate.rationales, strict=True
                            )
                        ]
                    else:
                        result.reason, result.wait_minutes = (
                            candidate.reason,
                            candidate.wait_minutes,
                        )
                    return
                result.checks.extend(errors)
                payload["previous_errors"] = feedback
            result.handoff, result.reason = True, "重写一次后仍未通过自检"
        except Exception:  # noqa: BLE001 -- redact provider and private storage failures
            result.handoff, result.reason = True, f"{current_step}失败，无法可靠生成草稿"
            result.messages = []
            result.drafts = []
