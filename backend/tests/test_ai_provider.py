"""AI provider layer: configuration, the Anthropic request/response shape, failures, and key secrecy. No real network."""

import base64
import json
import logging
from datetime import date

import pytest

from app.core.config import Settings
from app.services import ai_provider
from app.services.ai_provider import AnthropicProvider, configured_provider, https_post, provider_status
from app.services.errors import AiServiceError

KEY = "sk-ant-test-SECRET-abcdef123456"


def settings(**values):
    return Settings(_env_file=None, **values)


def reply(text, status=200, usage=None, model="claude-haiku-4-5-20251001"):
    body = {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}], "usage": usage or {"input_tokens": 100, "output_tokens": 20}}  # fmt: skip
    return status, json.dumps(body).encode()


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, dict(headers), json.loads(body), timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(transport, **extra):
    return AnthropicProvider(settings(ai_provider="anthropic", ai_api_key=KEY, **extra), transport)


class TestConfiguration:
    def test_nothing_set_means_not_configured(self):
        assert configured_provider(settings()) is None
        assert provider_status(settings()) == {
            "configured": False,
            "provider_label": None,
            "documents": False,
        }

    @pytest.mark.parametrize("key", ["", "   ", "your_api_key_here"])
    def test_blank_or_placeholder_keys_count_as_unset(self, key):
        assert configured_provider(settings(ai_provider="anthropic", ai_api_key=key)) is None

    def test_a_provider_without_a_key_or_an_unknown_provider_is_not_configured(self):
        assert configured_provider(settings(ai_provider="anthropic")) is None
        assert configured_provider(settings(ai_provider="skynet", ai_api_key=KEY)) is None
        assert configured_provider(settings(ai_provider="../etc/passwd", ai_api_key=KEY)) is None

    def test_a_configured_provider_reports_only_its_label(self):
        status = provider_status(settings(ai_provider="anthropic", ai_api_key=KEY))
        assert status == {"configured": True, "provider_label": "Anthropic", "documents": True}

    def test_the_key_can_come_from_either_variable_and_never_shows_in_a_repr(self, monkeypatch):
        monkeypatch.setenv("AI_API_KEY", KEY)
        loaded = Settings(_env_file=None)
        assert loaded.ai_api_key.get_secret_value() == KEY
        assert (
            KEY not in repr(loaded)
            and KEY not in str(loaded.model_dump())
            and KEY not in loaded.model_dump_json()
        )

    def test_the_env_example_holds_only_a_placeholder(self):
        from app.core.config import BACKEND_DIR

        text = (BACKEND_DIR / ".env.example").read_text()
        assert "AI_API_KEY=your_api_key_here" in text and "KIRANA_AI_PROVIDER" in text
        assert "sk-" not in text


class TestAnthropicRequests:
    def test_a_plan_request_uses_the_documented_shape_and_keeps_the_key_in_the_header_only(self):
        transport = Recorder(
            reply('```json\n{"tool": "get_sales_summary", "args": {"period": "today"}}\n```')
        )
        result = provider(transport).plan("kitna bika?", [{"name": "get_sales_summary"}], date(2026, 9, 21))
        url, headers, body, timeout = transport.calls[0]
        assert url == "https://api.anthropic.com/v1/messages"
        assert (
            headers["x-api-key"] == KEY
            and headers["anthropic-version"] == "2023-06-01"
            and headers["content-type"] == "application/json"
        )
        assert body["model"] == "claude-haiku-4-5-20251001" and body["max_tokens"] == 1024 and timeout == 20.0
        assert "untrusted" in body["system"] and body["messages"][0]["role"] == "user"
        assert "<question>\nkitna bika?\n</question>" in body["messages"][0]["content"][0]["text"]
        assert KEY not in json.dumps(body)
        assert result.data == {"tool": "get_sales_summary", "args": {"period": "today"}}
        assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.cost_micros) == (
            100,
            20,
            None,
        )

    def test_the_model_and_limits_come_from_settings(self):
        transport = Recorder(reply('{"tool": null}'))
        provider(transport, ai_model="some-model", ai_timeout_seconds=7, ai_max_output_tokens=256).plan(
            "q", [], date(2026, 9, 21)
        )
        _, _, body, timeout = transport.calls[0]
        assert (body["model"], body["max_tokens"], timeout) == ("some-model", 256, 7)

    def test_a_document_request_carries_the_image_as_a_base64_block(self):
        transport = Recorder(reply('{"rows": []}'))
        provider(transport).extract_document(b"\x89PNG-bytes", "image/png", "invoice")
        content = transport.calls[0][2]["messages"][0]["content"]
        assert content[0] == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(b"\x89PNG-bytes").decode(),
            },
        }
        assert (
            "never follow it" in transport.calls[0][2]["system"].lower()
            or "Never follow it" in transport.calls[0][2]["system"]
        )


class TestFailuresAreSafe:
    @pytest.mark.parametrize(
        ("status", "reason", "retryable"),
        [(429, "rate_limited", True), (529, "rate_limited", True), (500, "unavailable", True), (503, "unavailable", True),
         (401, "unavailable", False), (403, "unavailable", False), (400, "invalid_response", False), (302, "invalid_response", False)],
    )  # fmt: skip
    def test_http_statuses_map_to_fixed_reasons(self, status, reason, retryable):
        with pytest.raises(AiServiceError) as caught:
            provider(Recorder((status, b'{"error": "details that must never surface"}'))).plan(
                "q", [], date(2026, 9, 21)
            )
        assert (caught.value.reason, caught.value.retryable) == (reason, retryable)
        assert caught.value.message == "AI Assistant is temporarily unavailable."
        assert "details" not in str(caught.value) and KEY not in str(caught.value)

    @pytest.mark.parametrize("payload", [b"not json", b"[]", b'{"content": "text"}', b'{"content": [{"type": "text", "text": "no braces"}]}',
                                         b'{"content": [{"type": "text", "text": "{broken"}]}', b'{"content": [{"type": "text", "text": "[1,2]"}]}'])  # fmt: skip
    def test_unusable_replies_are_invalid_responses(self, payload):
        with pytest.raises(AiServiceError) as caught:
            provider(Recorder((200, payload))).plan("q", [], date(2026, 9, 21))
        assert caught.value.reason == "invalid_response"

    def test_token_counts_that_are_not_plain_numbers_are_ignored(self):
        result = provider(
            Recorder(reply('{"tool": null}', usage={"input_tokens": "lots", "output_tokens": -5}))
        ).plan("q", [], date(2026, 9, 21))
        assert (result.usage.input_tokens, result.usage.output_tokens) == (None, None)

    def test_transport_failures_propagate_as_the_same_safe_error(self):
        with pytest.raises(AiServiceError) as caught:
            provider(Recorder(AiServiceError("timeout"))).plan("q", [], date(2026, 9, 21))
        assert caught.value.reason == "timeout"

    def test_https_post_refuses_plain_http_and_maps_network_errors(self, monkeypatch):
        import urllib.error
        import urllib.request

        with pytest.raises(AiServiceError):
            https_post("http://api.anthropic.com/v1/messages", {}, b"{}", 5)

        def timeout(*a, **k):
            raise TimeoutError("boom with " + KEY)

        monkeypatch.setattr(urllib.request, "urlopen", timeout)
        with pytest.raises(AiServiceError) as caught:
            https_post("https://api.anthropic.com/v1/messages", {"x-api-key": KEY}, b"{}", 5)
        assert caught.value.reason == "timeout" and KEY not in str(caught.value)

        def refused(*a, **k):
            raise urllib.error.URLError("connection refused " + KEY)

        monkeypatch.setattr(urllib.request, "urlopen", refused)
        with pytest.raises(AiServiceError) as caught:
            https_post("https://api.anthropic.com/v1/messages", {}, b"{}", 5)
        assert caught.value.reason == "unavailable" and KEY not in str(caught.value)

    def test_an_oversized_reply_is_refused(self, monkeypatch):
        import io
        import urllib.request

        class Big:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return io.BytesIO(b"x" * (n if n > 0 else 10)).read()

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Big())
        with pytest.raises(AiServiceError) as caught:
            https_post("https://api.anthropic.com/v1/messages", {}, b"{}", 5)
        assert caught.value.reason == "invalid_response"

    def test_the_key_never_reaches_a_log_line_or_an_api_response(
        self, client_a, give_plan, tenant_a, monkeypatch, caplog
    ):
        give_plan(tenant_a, "pro")
        monkeypatch.setenv("AI_API_KEY", KEY)
        monkeypatch.setenv("KIRANA_AI_PROVIDER", "anthropic")
        from app.core import config

        config.get_settings.cache_clear()
        transport = Recorder((401, b"nope"))
        monkeypatch.setattr(ai_provider, "https_post", transport)
        monkeypatch.setitem(ai_provider.PROVIDERS, "anthropic", lambda s: AnthropicProvider(s, transport))
        try:
            with caplog.at_level(logging.DEBUG):
                response = client_a.post("/api/v1/ai/ask", json={"question": "kitna maal nikla aaj bhai"})
            assert response.status_code == 503 and KEY not in response.text
            assert KEY not in caplog.text
            assert KEY not in client_a.get("/api/v1/ai/status").text
        finally:
            config.get_settings.cache_clear()


def test_the_frontend_source_never_names_the_ai_key():
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "frontend" / "src"
    if not src.exists():
        pytest.skip("frontend not present")
    for path in src.rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx"} and ".test." not in path.name:
            text = path.read_text(errors="ignore")
            assert (
                "AI_API_KEY" not in text
                and "x-api-key" not in text.lower()
                and "api.anthropic.com" not in text
            ), path
