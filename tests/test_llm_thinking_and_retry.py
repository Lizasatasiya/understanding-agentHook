import unittest
from unittest.mock import patch
import os

from understanding_agent.api_utils import call_nous_api, _is_meaningful_response, _extract_message_text
from understanding_agent.llm_manager import thinking_disabled, thinking_control_params


def _ok_body(content):
    import json
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})


def _empty_body():
    import json
    # The captured Nous gateway glitch: 200, stop, content=null, tokens charged
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}],
                       "usage": {"completion_tokens": 206}})


class TestThinkingControl(unittest.TestCase):
    def test_thinking_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_DISABLE_THINKING", None)
            self.assertTrue(thinking_disabled())

    def test_thinking_kill_switch(self):
        for v in ("off", "0", "false", "no"):
            with patch.dict(os.environ, {"UNDERSTANDING_AGENT_DISABLE_THINKING": v}):
                self.assertFalse(thinking_disabled(), v)
                self.assertEqual(thinking_control_params("nous"), {})

    def test_nous_provider_sends_reasoning_effort_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_DISABLE_THINKING", None)
            with patch.dict(os.environ, {"UNDERSTANDING_AGENT_PROVIDER": "nous"}):
                self.assertEqual(thinking_control_params(), {"reasoning_effort": "none"})

    def test_other_providers_get_no_thinking_params(self):
        # Strict OpenAI endpoints 400 on unknown params; only send where supported.
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_PROVIDER": "openai"}):
            self.assertEqual(thinking_control_params(), {})

    def test_call_nous_api_injects_thinking_params(self):
        seen = {}
        def fake_request(api_key, payload, timeout):
            seen.update(payload)
            return 200, _ok_body("[{\"question\": \"Why?\"}]")
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_PROVIDER": "nous"}), \
             patch("understanding_agent.api_utils._groq_request_once", side_effect=fake_request):
            call_nous_api("k", {"model": "m"})
        self.assertEqual(seen.get("reasoning_effort"), "none")


class TestEmptyContentRetry(unittest.TestCase):
    def test_empty_content_is_not_meaningful(self):
        self.assertFalse(_is_meaningful_response(200, _empty_body()))

    def test_ok_content_is_meaningful(self):
        self.assertTrue(_is_meaningful_response(200, _ok_body("hello")))

    def test_error_statuses_final_only_when_client_error(self):
        self.assertTrue(_is_meaningful_response(401, "unauthorized"))
        self.assertFalse(_is_meaningful_response(500, "boom"))
        self.assertFalse(_is_meaningful_response(429, "rate"))
        self.assertFalse(_is_meaningful_response(0, "timeout"))

    def test_unparseable_200_treated_as_final(self):
        self.assertTrue(_is_meaningful_response(200, "not json at all"))

    def test_call_retries_on_empty_content_then_succeeds(self):
        responses = [(200, _empty_body()), (200, _ok_body("[{\"question\": \"Why?\"}]"))]
        with patch("understanding_agent.api_utils._groq_request_once",
                   side_effect=responses) as mock_req, patch("time.sleep"):
            status, body = call_nous_api("k", {"model": "m"})
        self.assertEqual(status, 200)
        self.assertIn("Why?", body)
        self.assertEqual(mock_req.call_count, 2)

    def test_call_returns_last_empty_body_after_exhausting_retries(self):
        with patch("understanding_agent.api_utils._groq_request_once",
                   return_value=(200, _empty_body())) as mock_req, patch("time.sleep"):
            status, body = call_nous_api("k", {"model": "m"}, retries=2)
        self.assertEqual(status, 200)
        self.assertEqual(mock_req.call_count, 3)

    def test_extract_message_text_prefers_content(self):
        data = {"choices": [{"message": {"content": "answer", "reasoning": "thoughts"}}]}
        self.assertEqual(_extract_message_text(data), "answer")

    def test_extract_message_text_falls_back_to_reasoning(self):
        data = {"choices": [{"message": {"content": None, "reasoning": "only reasoning"}}]}
        self.assertEqual(_extract_message_text(data), "only reasoning")

    def test_extract_message_text_empty_glitch(self):
        data = {"choices": [{"message": {"content": None, "reasoning": None}}]}
        self.assertEqual(_extract_message_text(data), "")


if __name__ == "__main__":
    unittest.main()
