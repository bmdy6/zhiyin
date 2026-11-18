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


class Candidate(Contract):
    wait_minutes: Annotated[StrictInt, Field(ge=0, le=10)]
    messages: list[str]
    reason: Line


class Review(Contract):
    passed: bool
    issues: list[Line]
    handoff: bool
