from __future__ import annotations

from typing import Any

from openai import OpenAI

from tanuki_bot.config.config import get_model, get_openai_key


def get_client() -> OpenAI:
    key = get_openai_key()
    if not key:
        raise RuntimeError("Missing OpenAI API key. Run: tanuki setup")
    return OpenAI(api_key=key)


def text(prompt: str, system: str | None = None) -> str:
    client = get_client()
    model = get_model()

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = client.responses.create(
        model=model,
        input=messages,
    )

    # Extract plain text output
    out = []
    for item in resp.output:
        if item.type == "message":
            for c in item.content:
                if c.type == "output_text":
                    out.append(c.text)
    return "\n".join(out).strip()