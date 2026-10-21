"""AI providers: the local Ollama connector (against a stand-in Ollama server) and the ai-check command."""

import base64
import io
import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from app import cli
from app.ai import client as ai_client
from app.ai.client import AIError, OllamaProvider, image_block, text_block
from app.core.config import get_settings

SCHEMA = {"type": "object", "properties": {"bill_no": {"type": ["string", "null"]}}, "required": ["bill_no"]}


class FakeOllama:
    """A tiny HTTP server that answers /api/chat the way Ollama does and records each request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply: dict = {"content": '{"bill_no": "IPB/2026/4417"}'}
        self.status = 200
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "body": body})
                if fake.status != 200:
                    payload = {"error": "model 'qwen2.5vl:7b' not found"}
                else:
                    payload = {"model": body["model"], "message": {"role": "assistant",
                                                                   "content": fake.reply["content"]},
                               "done": True, "done_reason": fake.reply.get("done_reason", "stop"),
                               "prompt_eval_count": 812, "eval_count": 21}
                data = json.dumps(payload).encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):  # noqa: A002 - silence request logging
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ollama():
    server = FakeOllama()
    yield server
    server.close()


def page_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("L", (400, 200), 255).save(buffer, "PNG")
    return buffer.getvalue()


def content() -> list[dict]:
    return [text_block("Document type: final bill. 1 page(s) follow."), text_block("Page 1 image:"),
            image_block(page_png()), text_block("Page 1 OCR text (may contain errors):\nBILL NO: IPB/2026/44l7")]


def provider(url: str, vision: bool = True) -> OllamaProvider:
    return OllamaProvider(url, "qwen2.5vl:7b", 30, vision=vision, context=24576, keep_alive="30m")


def test_ollama_request_and_answer(ollama):
    result = provider(ollama.url).structured(system="Copy values.", content=content(), tool_name="record_document",
                                             tool_description="Record the values.", schema=SCHEMA, max_tokens=500)
    assert result.data == {"bill_no": "IPB/2026/4417"}
    assert (result.provider, result.model, result.input_tokens, result.output_tokens) == \
        ("ollama", "qwen2.5vl:7b", 812, 21)
    assert result.cost_usd == Decimal("0")
    sent = ollama.requests[0]
    assert sent["path"] == "/api/chat"
    body = sent["body"]
    assert body["format"] == SCHEMA and body["stream"] is False and body["keep_alive"] == "30m"
    assert body["options"] == {"temperature": 0, "num_ctx": 24576, "num_predict": 500}
    system, user = body["messages"]
    assert system["role"] == "system" and "Record the values." in system["content"]
    assert len(user["images"]) == 1 and base64.b64decode(user["images"][0])[:4] == b"\x89PNG"
    assert "Page 1 image:" not in user["content"] and "attached in page order" in user["content"]
    assert "IPB/2026/44l7" in user["content"] and '"bill_no"' in user["content"]   # OCR text + schema


def test_text_only_models_get_no_images(ollama):
    provider(ollama.url, vision=False).structured(system="s", content=content(), tool_name="t",
                                                  tool_description="d", schema=SCHEMA)
    user = ollama.requests[0]["body"]["messages"][1]
    assert "images" not in user and "OCR text" in user["content"]


def test_answer_in_code_fence_is_accepted(ollama):
    ollama.reply = {"content": 'Here it is:\n```json\n{"bill_no": "A-1"}\n```'}
    result = provider(ollama.url).structured(system="s", content=content(), tool_name="t", tool_description="d",
                                             schema=SCHEMA)
    assert result.data == {"bill_no": "A-1"}


def test_bad_answers_are_errors_not_crashes(ollama):
    ollama.reply = {"content": "I cannot read this page."}
    with pytest.raises(AIError, match="did not return JSON") as info:
        provider(ollama.url).structured(system="s", content=content(), tool_name="t", tool_description="d",
                                        schema=SCHEMA)
    assert not info.value.fatal
    ollama.reply = {"content": '{"bill_no": "A', "done_reason": "length"}
    with pytest.raises(AIError, match="cut off"):
        provider(ollama.url).structured(system="s", content=content(), tool_name="t", tool_description="d",
                                        schema=SCHEMA)


def test_missing_model_and_no_server_are_fatal(ollama):
    ollama.status = 404
    with pytest.raises(AIError, match="ollama pull qwen2.5vl:7b") as info:
        provider(ollama.url).structured(system="s", content=content(), tool_name="t", tool_description="d",
                                        schema=SCHEMA)
    assert info.value.fatal
    with pytest.raises(AIError, match="Is the Ollama app running") as info:
        provider("http://127.0.0.1:9").structured(system="s", content=content(), tool_name="t",
                                                  tool_description="d", schema=SCHEMA)
    assert info.value.fatal


def test_ollama_is_chosen_without_a_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_provider", "ollama")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_model", "qwen2.5vl:7b")
    monkeypatch.setattr(settings, "ai_base_url", "")
    monkeypatch.setattr(settings, "ai_extraction", "gaps")
    chosen = ai_client.get_provider()
    assert isinstance(chosen, OllamaProvider) and chosen.base_url == "http://host.docker.internal:11434"
    assert ai_client.cost(1_000_000, 1_000_000) == Decimal("0")
    monkeypatch.setattr(settings, "ai_extraction", "off")
    assert ai_client.get_provider() is None


def test_ai_check_command(ollama, monkeypatch, capsys):
    monkeypatch.setattr(ai_client, "get_provider", lambda: provider(ollama.url))
    cli.main(["ai-check"])
    out = capsys.readouterr().out
    assert "OK: the model can read page images." in out and "qwen2.5vl:7b" in out
    ollama.reply = {"content": '{"bill_no": null}'}
    cli.main(["ai-check"])
    assert "Text-only model?" in capsys.readouterr().out