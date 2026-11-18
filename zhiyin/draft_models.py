"""Validated LLM contracts; no arbitrary provider output reaches rendering."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

Line = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"^[^\r\n]+$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Analysis(Contract):
    summary: Line
    stage: Annotated[StrictInt, Field(ge=1, le=7)] | Literal["friend"]
    intent: Line
    mood: Line
    unknowns: Annotated[list[Line], Field(max_length=10)]


class Decision(Contract):
    action: Literal["接话", "追问", "分享自己", "推进", "先不回", "交给我"]
    reason: Line


Rule = Annotated[StrictInt, Field(ge=1, le=6)]


class Rationale(Contract):
    reference: Annotated[str, Field(pattern=r"^R[1-5]$")]
    rules: Annotated[list[Rule], Field(min_length=1, max_length=6)]


class RuleReview(Contract):
    rule: Rule
    passed: bool


class Candidate(Contract):
    wait_minutes: Annotated[StrictInt, Field(ge=0, le=10)]
    messages: list[str]
    reason: Line
    rationales: Annotated[list[Rationale], Field(max_length=3)] | None = None


class Review(Contract):
    passed: bool
    issues: list[Line]
    handoff: bool
    rules: Annotated[list[RuleReview], Field(max_length=6)] | None = None
