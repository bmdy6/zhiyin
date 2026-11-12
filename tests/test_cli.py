import subprocess
import sys


def command(*args):
    return subprocess.run(
        [sys.executable, "-m", "chat_twin.cli", *args], capture_output=True, text=True, check=False
    )


def test_cli_risk_and_manual_promote(tmp_path):
    chat = tmp_path / "chat.txt"
    chat.write_text("她: 你是不是机器人？")
    state = tmp_path / "state"
    result = command(
        "draft",
        "--platform",
        "soul",
        "--contact",
        "合成",
        "--input",
        str(chat),
        "--state-dir",
        str(state),
    )
    assert result.returncode == 0, result.stderr
    assert "[交给你]" in result.stdout and "[发送]" not in result.stdout
    result = command(
        "promote", "--platform", "soul", "--contact", "合成", "--state-dir", str(state)
    )
    assert result.returncode == 0
    assert (state / "contacts/soul/合成.md").exists()


def test_cli_invalid_input_writes_nothing(tmp_path):
    chat = tmp_path / "chat.txt"
    chat.write_text("我: 结束")
    state = tmp_path / "state"
    result = command(
        "draft",
        "--platform",
        "soul",
        "--contact",
        "合成",
        "--input",
        str(chat),
        "--state-dir",
        str(state),
    )
    assert result.returncode != 0
    assert not state.exists()


def test_state_override_cannot_write_non_temporary_location():
    result = command(
        "promote", "--platform", "soul", "--contact", "合成", "--state-dir", "/Users/shared-state"
    )
    assert result.returncode != 0
