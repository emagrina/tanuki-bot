from __future__ import annotations

from typing import Any, Iterable

from openai import OpenAI
from openai import RateLimitError, AuthenticationError, APIConnectionError, BadRequestError

from tanuki_bot.config.config import get_model, get_openai_key


def get_client() -> OpenAI:
    key = get_openai_key()
    if not key:
        raise RuntimeError("Missing OpenAI API key. Run: tanuki setup")
    return OpenAI(api_key=key)


def _extract_text(output: Iterable[Any]) -> str:
    chunks: list[str] = []
    for item in output:
        if getattr(item, "type", None) == "message":
            for c in getattr(item, "content", []):
                if getattr(c, "type", None) == "output_text":
                    chunks.append(c.text)
    return "\n".join(chunks).strip()


def text(prompt: str, system: str | None = None) -> str:
    client = get_client()
    model = get_model()

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = client.responses.create(
            model=model,
            input=messages,
        )
    except RateLimitError as e:
        # This includes insufficient_quota
        raise RuntimeError(
            "OpenAI API quota/billing issue (429). "
            "Go to OpenAI Platform → Billing/Usage and add credits or enable billing. "
            "Then retry `tanuki plan`."
        ) from e
    except AuthenticationError as e:
        raise RuntimeError(
            "OpenAI authentication failed. Your API key is invalid or revoked. "
            "Run `tanuki setup` and paste a valid key."
        ) from e
    except BadRequestError as e:
        raise RuntimeError(
            "OpenAI request rejected (400). This can happen if the model name is invalid. "
            "Check `tanuki model` and try a valid model (e.g. gpt-5-mini)."
        ) from e
    except APIConnectionError as e:
        raise RuntimeError(
            "OpenAI connection failed. Check your network and try again."
        ) from e

    result = _extract_text(resp.output)
    if not result:
        raise RuntimeError("Model returned empty response")
    return result