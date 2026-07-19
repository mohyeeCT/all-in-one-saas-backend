import json
import logging
import unittest
from unittest.mock import patch

import anthropic
import httpx

from utils import copy_gen


MODEL = "claude-sonnet-5"
TEST_API_KEY = "unit-test-api-key-never-log"
TEST_PROMPT = "Sensitive test prompt that must never be logged."
PRIVATE_THINKING = "Private reasoning that must never reach generated copy or logs."
PRIVATE_SIGNATURE = "private-test-signature"
VISIBLE_TEXT = "Visible answer"


def _message_payload(*, stop_reason="end_turn"):
    return {
        "id": "msg_sonnet5_contract",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [
            {
                "type": "thinking",
                "thinking": PRIVATE_THINKING,
                "signature": PRIVATE_SIGNATURE,
            },
            {"type": "text", "text": VISIBLE_TEXT},
        ],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def _sse_payload():
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_sonnet5_stream_contract",
                    "type": "message",
                    "role": "assistant",
                    "model": MODEL,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 13, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "thinking",
                    "thinking": "",
                    "signature": "",
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": PRIVATE_THINKING},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": PRIVATE_SIGNATURE},
            },
        ),
        (
            "content_block_stop",
            {"type": "content_block_stop", "index": 0},
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": VISIBLE_TEXT},
            },
        ),
        (
            "content_block_stop",
            {"type": "content_block_stop", "index": 1},
        ),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 7},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
        for event_name, data in events
    ).encode("utf-8")


def _client(handler):
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return anthropic.Anthropic(
        api_key=TEST_API_KEY,
        http_client=http_client,
        max_retries=0,
    )


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class AnthropicSonnet5ContractTests(unittest.TestCase):
    def test_exact_approved_sdk_is_installed(self):
        self.assertEqual(anthropic.__version__, "0.117.0")

    def test_nonstream_response_preserves_thinking_usage_and_visible_text_boundary(self):
        captured_requests = []

        def handler(request):
            captured_requests.append(json.loads(request.content))
            return httpx.Response(200, json=_message_payload(), request=request)

        client = _client(handler)
        self.addCleanup(client.close)
        message = client.messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": "Contract test"}],
        )

        self.assertEqual([block.type for block in message.content], ["thinking", "text"])
        self.assertEqual(message.content[0].thinking, PRIVATE_THINKING)
        self.assertEqual(message.content[0].signature, PRIVATE_SIGNATURE)
        self.assertEqual(message.stop_reason, "end_turn")
        self.assertEqual(message.usage.input_tokens, 11)
        self.assertEqual(message.usage.output_tokens, 7)
        self.assertEqual(copy_gen._extract_anthropic_text(message.content), VISIBLE_TEXT)
        self.assertNotIn(PRIVATE_THINKING, copy_gen._extract_anthropic_text(message.content))
        self.assertEqual(captured_requests[0]["model"], MODEL)

    def test_stream_preserves_thinking_signature_usage_and_visible_text_boundary(self):
        def handler(request):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse_payload(),
                request=request,
            )

        client = _client(handler)
        self.addCleanup(client.close)
        with client.messages.stream(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": "Contract test"}],
        ) as stream:
            visible_text = copy_gen._extract_anthropic_stream_text(stream)
            final_message = stream.get_final_message()

        self.assertEqual(visible_text, VISIBLE_TEXT)
        self.assertEqual(
            [block.type for block in final_message.content],
            ["thinking", "text"],
        )
        self.assertEqual(final_message.content[0].thinking, PRIVATE_THINKING)
        self.assertEqual(final_message.content[0].signature, PRIVATE_SIGNATURE)
        self.assertEqual(final_message.stop_reason, "end_turn")
        self.assertEqual(final_message.usage.input_tokens, 13)
        self.assertEqual(final_message.usage.output_tokens, 7)
        self.assertNotIn(PRIVATE_THINKING, visible_text)
        self.assertNotIn(PRIVATE_SIGNATURE, visible_text)

    def test_current_stop_reasons_are_accessible(self):
        for stop_reason in ("end_turn", "max_tokens", "refusal", "pause_turn"):
            with self.subTest(stop_reason=stop_reason):
                def handler(request, value=stop_reason):
                    return httpx.Response(
                        200,
                        json=_message_payload(stop_reason=value),
                        request=request,
                    )

                client = _client(handler)
                try:
                    message = client.messages.create(
                        model=MODEL,
                        max_tokens=100,
                        messages=[{"role": "user", "content": "Contract test"}],
                    )
                finally:
                    client.close()
                self.assertEqual(message.stop_reason, stop_reason)

    def test_real_sdk_serializes_existing_request_behavior_without_sensitive_application_logs(self):
        captured_requests = []

        def handler(request):
            captured_requests.append(json.loads(request.content))
            return httpx.Response(200, json=_message_payload(), request=request)

        client = _client(handler)
        self.addCleanup(client.close)
        capture = _CaptureHandler()
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        root_logger.addHandler(capture)
        root_logger.setLevel(logging.INFO)
        try:
            with patch.object(anthropic, "Anthropic", return_value=client):
                ordinary_text = copy_gen._call_claude(
                    TEST_API_KEY,
                    TEST_PROMPT,
                    max_tokens=100,
                    model=MODEL,
                )
                strategy_text = copy_gen._call_claude(
                    TEST_API_KEY,
                    TEST_PROMPT,
                    max_tokens=copy_gen.STRATEGY_BRIEF_MAX_TOKENS,
                    model=MODEL,
                    effort=copy_gen.STRATEGY_BRIEF_CLAUDE_EFFORT,
                )
        finally:
            root_logger.removeHandler(capture)
            root_logger.setLevel(previous_level)

        self.assertEqual(ordinary_text, VISIBLE_TEXT)
        self.assertEqual(strategy_text, VISIBLE_TEXT)
        self.assertEqual(
            captured_requests[0],
            {
                "max_tokens": 100,
                "messages": [{"role": "user", "content": TEST_PROMPT}],
                "model": MODEL,
            },
        )
        self.assertEqual(
            captured_requests[1],
            {
                "max_tokens": copy_gen.STRATEGY_BRIEF_MAX_TOKENS,
                "messages": [{"role": "user", "content": TEST_PROMPT}],
                "model": MODEL,
                "thinking": {"type": "adaptive"},
                "output_config": {
                    "effort": copy_gen.STRATEGY_BRIEF_CLAUDE_EFFORT,
                },
            },
        )
        logged = "\n".join(capture.messages)
        for sensitive_value in (
            TEST_API_KEY,
            TEST_PROMPT,
            PRIVATE_THINKING,
            PRIVATE_SIGNATURE,
            VISIBLE_TEXT,
        ):
            self.assertNotIn(sensitive_value, logged)


if __name__ == "__main__":
    unittest.main()
