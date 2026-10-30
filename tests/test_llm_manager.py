import unittest
import os
import json

from understanding_agent.llm_manager import (
    SUPPORTED_MODELS, get_provider, resolve_base_url, resolve_model,
    resolve_judge_model, resolve_api_key, LLMManager,
)
from understanding_agent.api_utils import (
    get_base_url, get_model, get_judge_model, load_nous_api_key,
    _normalize_response_body, _format_anthropic_payload,
)


def _clean_env(f):
    managed = (
        "UNDERSTANDING_AGENT_MODEL", "LLM_MODEL", "MODEL",
        "UNDERSTANDING_AGENT_API_KEY", "LLM_API_KEY", "API_KEY",
        "UNDERSTANDING_AGENT_JUDGE_MODEL",
        "UNDERSTANDING_AGENT_IGNORE_DOTENV",
        "NOUS_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
    )

    def wrapper(self):
        saved = {k: os.environ.get(k) for k in managed}
        for k in managed:
            os.environ.pop(k, None)
        os.environ["UNDERSTANDING_AGENT_IGNORE_DOTENV"] = "1"
        try:
            f(self)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return wrapper


class TestSupportedModels(unittest.TestCase):
    def test_only_three_models(self):
        self.assertEqual(set(SUPPORTED_MODELS), {"nous", "sonnet", "haiku"})

    @_clean_env
    def test_default_is_nous(self):
        self.assertEqual(get_provider(), "nous")
        self.assertEqual(resolve_model(), "qwen/qwen3-coder-30b-a3b-instruct")
        self.assertEqual(resolve_judge_model(), resolve_model())
        self.assertEqual(resolve_base_url(), "https://inference-api.nousresearch.com/v1")

    @_clean_env
    def test_sonnet_and_haiku_switch_questions_and_answers(self):
        expected = {
            "sonnet": ("anthropic", "claude-sonnet-5-5",
                       "https://api.anthropic.com/v1"),
            "haiku": ("anthropic", "claude-haiku-5-5",
                      "https://api.anthropic.com/v1"),
            "nous": ("nous", "qwen/qwen3-coder-30b-a3b-instruct",
                     "https://inference-api.nousresearch.com/v1"),
        }
        for name, (provider, model, url) in expected.items():
            os.environ["UNDERSTANDING_AGENT_MODEL"] = name
            os.environ["UNDERSTANDING_AGENT_JUDGE_MODEL"] = "not-used"
            self.assertEqual(get_provider(), provider, name)
            self.assertEqual(resolve_model(), model, name)
            self.assertEqual(get_model(), model, name)
            self.assertEqual(get_judge_model(), model, name)
            self.assertEqual(resolve_base_url(), url, name)
            self.assertEqual(get_base_url(), url, name)

    @_clean_env
    def test_unknown_model_name_falls_back_to_nous(self):
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "gpt-4o-mini"
        self.assertEqual(resolve_model(), "qwen/qwen3-coder-30b-a3b-instruct")
        self.assertEqual(resolve_base_url(), "https://inference-api.nousresearch.com/v1")

    @_clean_env
    def test_api_key_prefers_central_var(self):
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "sonnet"
        os.environ["UNDERSTANDING_AGENT_API_KEY"] = "central-key"
        os.environ["ANTHROPIC_API_KEY"] = "vendor-key"
        self.assertEqual(resolve_api_key(), "central-key")
        self.assertEqual(load_nous_api_key(), "central-key")

    @_clean_env
    def test_manager_caches_until_reset(self):
        m = LLMManager()
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "haiku"
        self.assertEqual(m.model, "claude-haiku-5-5")
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "sonnet"
        self.assertEqual(m.model, "claude-haiku-5-5")
        m.reset()
        self.assertEqual(m.model, "claude-sonnet-5-5")


class TestResponseNormalization(unittest.TestCase):
    def test_anthropic_response_normalized(self):
        anthropic_body = json.dumps({
            "content": [{"type": "text", "text": "Anthropic answer here"}],
            "stop_reason": "end_turn",
        })
        normalized = _normalize_response_body(anthropic_body, "claude-3-5-sonnet")
        data = json.loads(normalized)
        self.assertEqual(data["choices"][0]["message"]["content"], "Anthropic answer here")

    def test_openai_response_untouched(self):
        openai_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OpenAI answer"}}]
        })
        self.assertEqual(_normalize_response_body(openai_body, "gpt-4o"), openai_body)


class TestAnthropicPayloadFormatting(unittest.TestCase):
    def test_system_prompt_extracted_to_top_level(self):
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [
                {"role": "system", "content": "You are a code reviewer."},
                {"role": "user", "content": "Review this patch."},
            ],
            "temperature": 0.2,
        }
        anthropic_payload = _format_anthropic_payload(payload)
        self.assertEqual(anthropic_payload["system"], "You are a code reviewer.")
        self.assertEqual(anthropic_payload["messages"][0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
