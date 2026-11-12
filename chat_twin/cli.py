"""Command-line drafts only: no applications, UI automation or message sending."""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from chat_twin.contacts import ContactStore
from chat_twin.draft import DraftEngine
from chat_twin.llm import DeepSeek
from chat_twin.resources import ROOT
from chat_twin.sample_client import SampleClient


def temporary_state(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    resolved = path.resolve()
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if not any(resolved != root and resolved.is_relative_to(root) for root in roots):
        raise argparse.ArgumentTypeError("--state-dir仅供测试，必须是系统临时目录下的子目录")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("draft", "promote"):
        command = commands.add_parser(name)
        command.add_argument("--platform", required=True)
        command.add_argument("--contact", required=True)
        command.add_argument(
            "--state-dir", type=temporary_state, help="仅测试用的临时目录；默认 ~/.chat-twin/"
        )
        if name == "draft":
            command.add_argument("--input", required=True, type=Path)
            command.add_argument("--goal")
            command.add_argument("--index-dir", type=Path, default=ROOT / "data/index")
            command.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    args = parser.parse_args()
    store = ContactStore(args.state_dir)
    try:
        if args.command == "promote":
            store.promote(args.platform, args.contact)
            print("已升级为独立联系人档案")
            return
        # Read bounded input; failures never print contents or a provider traceback.
        with args.input.expanduser().open(encoding="utf-8") as handle:
            text = handle.read(10001)

        async def draft():
            async with SampleClient(args.index_dir, args.model_cache) as samples:
                return await DraftEngine(DeepSeek(), samples, store).run(
                    args.platform, args.contact, text, goal=args.goal
                )

        print(asyncio.run(draft()).render())
    except (ValueError, OSError):
        print("输入或联系人存储不可用，请检查格式、路径和权限", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:  # noqa: BLE001 -- redact private provider/input errors at boundary
        print("草稿流程失败，未自动发送任何消息", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
