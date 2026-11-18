"""Command-line drafts only: no applications, UI automation or message sending."""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from zhiyin.brief import contact_brief
from zhiyin.contacts import ContactStore
from zhiyin.draft import DraftEngine
from zhiyin.llm import DeepSeek
from zhiyin.my_replies import ReplySearch
from zhiyin.resources import ROOT
from zhiyin.sample_client import SampleClient
from zhiyin.tone import tone_profile
from zhiyin.wechat import EXPORT, WeChatStore, import_wechat


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
        command.add_argument("--platform", choices=["wechat"], default="wechat")
        command.add_argument("--contact", required=True)
        command.add_argument(
            "--state-dir", type=temporary_state, help="仅测试用的临时目录；默认 ~/.zhiyin/"
        )
        if name == "draft":
            command.add_argument("--input", required=True, type=Path)
            command.add_argument("--goal")
            command.add_argument("--index-dir", type=Path, default=ROOT / "data/index")
            command.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    importer = commands.add_parser("import_wechat", aliases=["import-wechat"])
    importer.add_argument("--source", type=Path, default=EXPORT)
    importer.add_argument("--state-dir", type=temporary_state)
    importer.add_argument("--prepare-search", action="store_true")
    importer.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    tone = commands.add_parser("tone_profile", aliases=["tone-profile"])
    tone.add_argument("--state-dir", type=temporary_state)
    brief = commands.add_parser("contact_brief", aliases=["contact-brief"])
    brief.add_argument("contact")
    brief.add_argument("--state-dir", type=temporary_state)
    search = commands.add_parser("search_my_replies", aliases=["search-my-replies"])
    search.add_argument("message")
    search.add_argument("--contact")
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--model-cache", type=Path, default=ROOT / "data/cache/models")
    search.add_argument("--state-dir", type=temporary_state)
    args = parser.parse_args()
    store = ContactStore(args.state_dir)
    try:
        if args.command in {"import_wechat", "import-wechat"}:
            wechat = WeChatStore(args.state_dir)
            stats = import_wechat(wechat, args.source)
            if args.prepare_search:
                stats["search_contexts"] = ReplySearch(wechat, args.model_cache).prepare()
            print(json.dumps(stats))
            return
        if args.command in {"tone_profile", "tone-profile"}:
            print(json.dumps(tone_profile(WeChatStore(args.state_dir))))
            return
        if args.command in {"contact_brief", "contact-brief"}:
            print(
                json.dumps(
                    contact_brief(WeChatStore(args.state_dir), args.contact), ensure_ascii=False
                )
            )
            return
        if args.command in {"search_my_replies", "search-my-replies"}:
            replies = ReplySearch(WeChatStore(args.state_dir), args.model_cache)
            print(
                json.dumps(replies.search(args.message, args.contact, args.k), ensure_ascii=False)
            )
            return
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


def legacy_main():
    print("chat-twin 已更名为 zhiyin", file=sys.stderr)
    main()


if __name__ == "__main__":
    main()
