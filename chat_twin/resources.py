"""Public prompt/profile resources, including installed-wheel paths."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resource(relative: str) -> str:
    root = ROOT if (ROOT / "prompts/analyze.txt").is_file() else Path(__file__).parent / "resources"
    return (root / relative).read_text(encoding="utf-8")
