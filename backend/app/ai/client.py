"""Structured calls to an AI model, with retries, timing and a cost estimate.

Providers (AI_PROVIDER):
  ollama     a model running on your own machine (no key, no cost, nothing leaves the machine)
  anthropic  Claude through the Anthropic API (needs AI_API_KEY and credit)
Another provider is a new class with the same `structured()` method.
"""

import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from PIL import Image

from app.core.config import get_settings

log = logging.getLogger(__name__)


class AIError(Exception):
    """A failed call. `fatal` means every further call would fail too (no server, no model, no credit)."""

    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


@dataclass
class AIResult:
    data: dict
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    duration_ms: int


class AIProvider(Protocol):
    name: str
    model: str

    def structured(self, *, system: str, content: list[dict], tool_name: str, tool_description: str,
                   schema: dict, max_tokens: int = 4096) -> AIResult: ...


def cost(input_tokens: int, output_tokens: int) -> Decimal:
    """Estimated API cost. Local models cost nothing per call."""
    s = get_settings()
    if s.ai_provider.strip().lower() == "ollama":
        return Decimal("0")
    usd = (input_tokens * s.ai_price_input_per_mtok + output_tokens * s.ai_price_output_per_mtok) / 1_000_000
    return Decimal(str(round(usd, 5)))


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(png: bytes) -> dict:
    """Page image for the model, scaled so its long side is at most AI_IMAGE_MAX_SIDE pixels."""
    img = Image.open(io.BytesIO(png))
    limit = get_settings().ai_image_max_side
    if max(img.size) > limit:
        ratio = limit / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.convert("L").save(buffer, "PNG", optimize=True)
    data = base64.b64encode(buffer.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: int):
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=timeout)

    def structured(self, *, system: str, content: list[dict], tool_name: str, tool_description: str,
                   schema: dict, max_tokens: int = 4096) -> AIResult:
        import anthropic

        started = time.monotonic()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],  # type: ignore[list-item]
                tools=[{"name": tool_name, "description": tool_description, "input_schema": schema}],  # type: ignore[list-item]
                tool_choice={"type": "tool", "name": tool_name},
            )
        except anthropic.APIError as exc:
            fatal = isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                                     anthropic.NotFoundError)) or "credit balance" in str(exc)
            raise AIError(f"{exc.__class__.__name__}: {exc}", fatal=fatal) from exc
        duration = int((time.monotonic() - started) * 1000)
        block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise AIError(f"Model returned no structured answer (stop reason: {response.stop_reason})")
        usage = response.usage
        return AIResult(dict(block.input), self.name, self.model, usage.input_tokens, usage.output_tokens,  # type: ignore[call-overload]
                        cost(usage.input_tokens, usage.output_tokens), duration)


OLLAMA_DEFAULT_URL = "http://host.docker.internal:11434"   # Ollama on the Mac, seen from a Docker container


def _parse_json(text: str) -> dict:
    """The model's answer as a dict. Tolerates ```json fences or a sentence around the object."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise AIError(f"Model did not return JSON: {text[:200]!r}") from None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise AIError(f"Model returned broken JSON ({exc.msg}): {text[:200]!r}") from None
    if not isinstance(data, dict):
        raise AIError(f"Model returned {type(data).__name__}, expected an object")
    return data


class OllamaProvider:
    """A local model served by Ollama (https://ollama.com), e.g. qwen2.5vl:7b.

    Uses /api/chat with `format` set to the JSON schema, so the model can only answer in that shape.
    Vision models get the page images; with AI_VISION=false only the OCR text is sent (text-only models).
    """

    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: int, *, vision: bool, context: int, keep_alive: str):
        import httpx

        self.model = model
        self.base_url = base_url.rstrip("/")
        self.vision = vision
        self.context = context
        self.keep_alive = keep_alive
        self._http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=10))

    def _message(self, content: list[dict], schema: dict) -> dict:
        texts: list[str] = []
        images: list[str] = []
        for block in content:
            if block.get("type") == "text":
                if re.fullmatch(r"Page \d+ image:", block["text"]):   # images travel separately in Ollama
                    continue
                texts.append(block["text"])
            elif block.get("type") == "image" and self.vision:
                images.append(block["source"]["data"])
        if images:
            texts.insert(1, f"The {len(images)} page image(s) are attached in page order.")
        texts.append("Answer with one JSON object only, matching this schema:\n"
                     + json.dumps(schema, ensure_ascii=False))
        message: dict = {"role": "user", "content": "\n\n".join(texts)}
        if images:
            message["images"] = images
        return message

    def _post(self, body: dict) -> dict:
        import httpx

        last: Exception | None = None
        for _attempt in range(2):                       # one retry for a dropped connection
            try:
                response = self._http.post("/api/chat", json=body)
            except httpx.ConnectError as exc:
                raise AIError(f"Cannot reach Ollama at {self.base_url}. Is the Ollama app running? ({exc})",
                              fatal=True) from exc
            except httpx.TimeoutException as exc:
                raise AIError(f"Ollama took longer than the timeout (AI_TIMEOUT_SECONDS): {exc}") from exc
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(2)
                continue
            if response.status_code == 404:
                raise AIError(f"Model {self.model!r} is not in Ollama. Run: ollama pull {self.model}", fatal=True)
            if response.status_code >= 400:
                try:
                    detail = response.json().get("error", response.text)
                except ValueError:
                    detail = response.text
                raise AIError(f"Ollama error {response.status_code}: {str(detail)[:300]}")
            return response.json()
        raise AIError(f"Ollama request failed: {last}")

    def structured(self, *, system: str, content: list[dict], tool_name: str, tool_description: str,
                   schema: dict, max_tokens: int = 4096) -> AIResult:
        started = time.monotonic()
        body = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_ctx": self.context, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": f"{system}\nTask: {tool_description}"},
                         self._message(content, schema)],
        }
        reply = self._post(body)
        text = (reply.get("message") or {}).get("content") or ""
        if reply.get("done_reason") == "length":
            raise AIError("Answer was cut off: raise AI_CONTEXT_TOKENS or lower AI_IMAGE_MAX_SIDE")
        data = _parse_json(text)
        duration = int((time.monotonic() - started) * 1000)
        tokens_in, tokens_out = int(reply.get("prompt_eval_count") or 0), int(reply.get("eval_count") or 0)
        return AIResult(data, self.name, self.model, tokens_in, tokens_out, Decimal("0"), duration)


@lru_cache
def _provider(provider: str, key: str, model: str, timeout: int, base_url: str, vision: bool, context: int,
              keep_alive: str) -> AIProvider | None:
    if provider == "anthropic" and key:
        return AnthropicProvider(key, model, timeout)
    if provider == "ollama" and model:
        return OllamaProvider(base_url or OLLAMA_DEFAULT_URL, model, timeout, vision=vision, context=context,
                              keep_alive=keep_alive)
    if provider and provider not in ("anthropic", "ollama"):
        log.warning("AI provider %r is not supported; running without AI", provider)
    return None


def get_provider() -> AIProvider | None:
    """The provider for reading documents, or None (rules only) when AI_EXTRACTION is off or no AI is set up."""
    s = get_settings()
    if s.ai_extraction == "off":
        return None
    return _configured()


def get_check_provider() -> AIProvider | None:
    """The provider for the AI judgement checks, or None when AI_CHECKS is false or no AI is set up."""
    return _configured() if get_settings().ai_checks else None


def _configured() -> AIProvider | None:
    from app.core.hardening import external_ai_blocked

    s = get_settings()
    if external_ai_blocked(s):
        # second line of defence: the API and worker refuse to start like this, but a CLI command could not
        log.error("AI provider %r is not allowed outside development without ALLOW_EXTERNAL_AI=true; rules only",
                  s.ai_provider)
        return None
    return _provider(s.ai_provider.strip().lower(), s.ai_api_key.strip(), s.ai_model.strip(), s.ai_timeout_seconds,
                     s.ai_base_url.strip(), s.ai_vision, s.ai_context_tokens, s.ai_keep_alive)
