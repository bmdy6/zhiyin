"""DeepSeek JSON calls. Never log prompts, profiles, provider bodies or secrets."""

import asyncio
import json
from pathlib import Path

import httpx

from zhiyin.resources import resource

MODEL = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"


def read_key() -> str:
    vault = json.loads((Path.home() / ".config/ai-vault/vault.json").read_text())
    key = vault["model-providers"]["deepseek"]["DEEPSEEK_API_KEY"]["value"]
    if not isinstance(key, str) or not key:
        raise ValueError("个人 DeepSeek key 不可用")
    return key


class DeepSeek:
    def __init__(self, *, transport=None):
        self.transport = transport
        self.metadata = []

    async def complete(self, step: str, payload: dict) -> dict:
        if step not in {"analyze", "decide", "write", "check"}:
            raise ValueError("Unknown prompt step")
        body = {
            "model": MODEL,
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 1800,
            "messages": [
                {"role": "system", "content": resource(f"prompts/{step}.txt")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        # Bounded retries only for transport/429/5xx. Invalid model contracts are handled by engine.
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
                    response = await client.post(
                        ENDPOINT, headers={"Authorization": "Bearer " + read_key()}, json=body
                    )
                    response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                if choice["finish_reason"] != "stop" or choice["message"].get("reasoning_content"):
                    raise ValueError("Incomplete or thinking response")
                result = json.loads(choice["message"]["content"])
                if not isinstance(result, dict):
                    raise TypeError("JSON object required")
                # Only allow non-sensitive model/usage metadata, never raw request/response.
                usage = data.get("usage") or {}
                self.metadata.append(
                    {
                        "step": step,
                        "model": data.get("model"),
                        "usage": {
                            key: value
                            for key, value in usage.items()
                            if key.endswith("_tokens") and type(value) is int
                        },
                    }
                )
                return result
            except httpx.HTTPStatusError as error:
                if attempt == 2 or (
                    error.response.status_code != 429 and error.response.status_code < 500
                ):
                    raise RuntimeError("模型请求失败") from None
            except httpx.TransportError:
                if attempt == 2:
                    raise RuntimeError("模型连接失败") from None
            await asyncio.sleep(2**attempt)
        raise RuntimeError("模型请求失败")
