"""Analyze → decide → MCP search → write → deterministic/semantic self-check."""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from zhiyin.contacts import ContactStore, validate_identity
from zhiyin.draft_models import Analysis, Candidate, Decision, Review
from zhiyin.resources import resource
from zhiyin.safety import (
    draft_errors,
    handoff_reason,
    parse_chat,
    profile_echo,
    review_categories,
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

    def render(self) -> str:
        lines = [f"[提示] {notice}" for notice in self.notices]
        if self.handoff:
            lines.append("[交给你] " + self.reason)
        elif not self.messages:
            lines.extend(["[先不回]", "[理由] " + self.reason])
        else:
            lines.append(f"[等待 {self.wait_minutes}分钟]")
            lines.extend("[发送] " + text for text in self.messages)
            lines.append("[理由] " + self.reason)
        return "\n".join(lines)


class DraftEngine:
    def __init__(self, model, samples, store: ContactStore, *, style_path: Path | None = None):
        self.model, self.samples, self.store = model, samples, store
        self.style_path = (
            style_path if style_path is not None else Path.home() / ".style-polish/spoken.md"
        )

    async def run(
        self, platform: str, contact: str, text: str, *, goal: str | None = None
    ) -> DraftResult:
        validate_identity(platform, contact)
        chat = parse_chat(text)
        if goal is not None and (not goal.strip() or len(goal) > 500):
            raise ValueError("goal须为1–500字符")
        result = DraftResult()
        current_step = "读取联系人"
        try:
            context = self.store.context(platform, contact)
            payload = {"chat": chat, "contact_context": context, "goal": goal}
            direct = handoff_reason(chat[-1]["text"]) or handoff_reason(goal or "")
            if direct:
                result.handoff, result.reason = True, direct
            else:
                current_step = "分析"
                analysis = Analysis.model_validate(await self.model.complete("analyze", payload))
                result.analysis = analysis
                payload["analysis"] = analysis.model_dump()
                if len(analysis.unknowns) >= 3:
                    result.handoff, result.reason = True, "关键信息不足，分析有至少3项未知"
                else:
                    current_step = "决策"
                    decision = Decision.model_validate(await self.model.complete("decide", payload))
                    payload["decision"] = decision.model_dump()
                    if decision.action in {"交给我", "先不回"}:
                        result.handoff = decision.action == "交给我"
                        result.reason = decision.reason
                    else:
                        current_step = "检索"
                        rows = await self.samples.search(
                            chat[-1]["text"],
                            stage=None if analysis.stage == "friend" else analysis.stage,
                            k=5,
                        )
                        result.sample_ids = [row["id"] for row in rows]
                        payload["samples"] = rows
                        current_step = "读取文风"
                        if self.style_path.is_file():
                            style = self.style_path.read_text(encoding="utf-8")
                            if not style.strip() or len(style) > 100000:
                                raise ValueError("文风档案为空或过长")
                        else:
                            style = resource("profiles/example.md")
                            result.notices.append("未找到文风档案，使用 profiles/example.md")
                        payload["spoken_style"] = style
                        payload["principles_source"] = resource("vendor/tong-jincheng/SKILL.md")
                        for attempt in range(2):
                            result.attempts += 1
                            current_step = "生成"
                            try:
                                candidate = Candidate.model_validate(
                                    await self.model.complete("write", payload)
                                )
                                errors = draft_errors(candidate.messages, rows)
                                if profile_echo(
                                    "".join([*candidate.messages, candidate.reason]), style
                                ):
                                    errors.append("草稿疑似复述私密文风档案")
                            except ValidationError:
                                errors = ["草稿JSON字段不符合契约"]
                                candidate = None
                            feedback = errors
                            if not errors:
                                current_step = "自检"
                                review_payload = dict(payload, candidate=candidate.model_dump())
                                review = Review.model_validate(
                                    await self.model.complete("check", review_payload)
                                )
                                if review.handoff:
                                    result.handoff, result.reason = (
                                        True,
                                        "自检发现需要本人处理的风险",
                                    )
                                    break
                                if not review.passed or review.issues:
                                    errors = [
                                        "模型语义自检未通过："
                                        + "、".join(review_categories(review.issues))
                                    ]
                                    feedback = review.issues or errors
                            if not errors:
                                result.messages = candidate.messages
                                result.reason = candidate.reason
                                result.wait_minutes = candidate.wait_minutes
                                break
                            result.checks.extend(errors)
                            payload["previous_errors"] = feedback
                        else:
                            result.handoff, result.reason = True, "重写一次后仍未通过自检"
        except Exception:  # noqa: BLE001 -- redact private provider/input errors at boundary
            # Provider/validation/storage errors may contain private input. Do not surface raw errors.
            result.handoff, result.reason = True, f"{current_step}失败，无法可靠生成草稿"
            result.messages = []
        try:
            summary = (
                result.analysis.summary
                if result.analysis
                else "本次输入需本人查看，未生成可发送草稿"
            )
            self.store.update(
                platform, contact, summary, result.analysis.stage if result.analysis else None
            )
        except Exception:  # noqa: BLE001 -- redact private provider/input errors at boundary
            result.notices.append("联系人记录更新失败，请检查本地存储")
        return result
