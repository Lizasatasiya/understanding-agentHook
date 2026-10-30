import unittest
from unittest.mock import patch
import os
import json

from understanding_agent.llm_manager import (
    PROVIDER_PRESETS, PROVIDER_ALIASES, get_provider, resolve_base_url,
    resolve_model, resolve_api_key, LLMManager,
    detect_provider_from_model, detect_provider_from_key,
)
from understanding_agent.api_utils import (
    get_base_url, get_model, load_nous_api_key,
    _normalize_response_body, _format_anthropic_payload,
)


def _clean_env(f):
    """Run a test with all manager-relevant env vars cleared."""
    managed = (
        "UNDERSTANDING_AGENT_PROVIDER", "UNDERSTANDING_AGENT_MODEL",
        "UNDERSTANDING_AGENT_BASE_URL", "UNDERSTANDING_AGENT_API_KEY",
        "UNDERSTANDING_AGENT_JUDGE_MODEL",
        "LLM_PROVIDER", "PROVIDER", "LLM_MODEL", "MODEL",
        "LLM_BASE_URL", "BASE_URL", "LLM_API_KEY", "API_KEY",
        "NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY",
        "NOUS_MODEL", "NOUS_BASE_URL", "GROQ_API_KEY", "GROQ_MODEL",
        "GROQ_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL",
        "TOGETHER_API_KEY", "TOGETHER_MODEL", "TOGETHER_BASE_URL",
        "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_BASE_URL",
        "GOOGLE_API_KEY", "GOOGLE_MODEL", "GOOGLE_BASE_URL", "GOOGLE_AI_API_KEY",
        "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY", "CLAUDE_MODEL", "CLAUDE_BASE_URL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL",
        "MISTRAL_API_KEY", "MISTRAL_MODEL", "MISTRAL_BASE_URL",
        "PERPLEXITY_API_KEY", "PERPLEXITY_MODEL", "PERPLEXITY_BASE_URL",
        "XAI_API_KEY", "XAI_MODEL", "XAI_BASE_URL", "GROK_API_KEY", "GROK_MODEL",
        "COHERE_API_KEY", "COHERE_MODEL", "COHERE_BASE_URL",
        "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "CEREBRAS_BASE_URL",
        "SAMBANOVA_API_KEY", "SAMBANOVA_MODEL", "SAMBANOVA_BASE_URL",
        "OLLAMA_API_KEY", "OLLAMA_MODEL", "OLLAMA_BASE_URL",
        "UNDERSTANDING_AGENT_IGNORE_DOTENV",
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


class TestProviderPresets(unittest.TestCase):
    def test_all_presets_have_required_fields(self):
        for name, preset in PROVIDER_PRESETS.items():
            self.assertIn("base_url", preset, name)
            self.assertIn("model", preset, name)
            self.assertIn("api_key_env", preset, name)

    def test_known_providers_present(self):
        expected = (
            "nous", "groq", "openai", "gemini", "anthropic", "deepseek",
            "mistral", "openrouter", "together", "perplexity", "xai",
            "cohere", "cerebras", "sambanova", "ollama", "custom", "yorkdevs"
        )
        for name in expected:
            self.assertIn(name, PROVIDER_PRESETS)

    def test_groq_default_model_is_real_production_id(self):
        self.assertEqual(
            PROVIDER_PRESETS["groq"]["model"], "llama-3.3-70b-versatile")


class TestProviderResolution(unittest.TestCase):
    @_clean_env
    def test_default_provider_is_org_gateway(self):
        self.assertEqual(get_provider(), "yorkdevs")
        self.assertEqual(resolve_base_url(), "http://llm.yorkdevs.link:2222/v1")
        self.assertEqual(resolve_model(), "default")

    @_clean_env
    def test_provider_env_selects_preset(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "groq"
        self.assertEqual(get_provider(), "groq")
        self.assertEqual(resolve_base_url(), "https://api.groq.com/openai/v1")
        self.assertEqual(resolve_model(), "llama-3.3-70b-versatile")

    @_clean_env
    def test_unknown_provider_falls_back_to_org_gateway(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "does-not-exist"
        self.assertEqual(get_provider(), "yorkdevs")

    @_clean_env
    def test_one_env_var_switches_provider_and_uses_preset_model(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "openai"
        self.assertEqual(resolve_base_url(), "https://api.openai.com/v1")
        self.assertEqual(resolve_model(), "gpt-4o-mini")

    @_clean_env
    def test_model_override_within_provider(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "groq"
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "llama-3.1-8b-instant"
        self.assertEqual(resolve_model(), "llama-3.1-8b-instant")
        self.assertEqual(resolve_base_url(), "https://api.groq.com/openai/v1")

    @_clean_env
    def test_base_url_override_enables_any_openai_compatible(self):
        os.environ["UNDERSTANDING_AGENT_BASE_URL"] = "https://llm.internal:8080/v1"
        os.environ["UNDERSTANDING_AGENT_API_KEY"] = "local-key"
        self.assertEqual(resolve_base_url(), "https://llm.internal:8080/v1")
        self.assertEqual(resolve_api_key(), "local-key")

    @_clean_env
    def test_legacy_nous_env_still_resolves(self):
        os.environ["NOUS_API_KEY"] = "legacy-key"
        os.environ["NOUS_MODEL"] = "some/model"
        self.assertEqual(resolve_api_key(), "legacy-key")
        self.assertEqual(resolve_model(), "some/model")

    @_clean_env
    def test_legacy_groq_key_shape_sniffs_groq(self):
        os.environ["NOUS_API_KEY"] = "gsk_legacy_shape_key_12345"
        self.assertEqual(resolve_base_url(), "https://api.groq.com/openai/v1")
        self.assertEqual(resolve_model(), "llama-3.3-70b-versatile")

    @_clean_env
    def test_groq_provider_key_env(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "groq"
        os.environ["GROQ_API_KEY"] = "gsk_xyz"
        self.assertEqual(resolve_api_key(), "gsk_xyz")

    @_clean_env
    def test_central_api_key_beats_preset_var(self):
        os.environ["UNDERSTANDING_AGENT_API_KEY"] = "central"
        os.environ["GROQ_API_KEY"] = "preset"
        self.assertEqual(resolve_api_key(), "central")

    @_clean_env
    def test_ollama_needs_no_key(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "ollama"
        self.assertEqual(resolve_base_url(), "http://localhost:11434/v1")
        self.assertEqual(resolve_api_key(), "")


class TestModelAgnosticResolution(unittest.TestCase):
    """Verify that adding an API key and/or model name automatically configures the LLM."""

    @_clean_env
    def test_auto_detect_gemini_from_key(self):
        os.environ["GEMINI_API_KEY"] = "AIzaSy_gemini_test_key"
        self.assertEqual(get_provider(), "gemini")
        self.assertEqual(resolve_base_url(), "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(resolve_model(), "gemini-2.0-flash")
        self.assertEqual(resolve_api_key(), "AIzaSy_gemini_test_key")

    @_clean_env
    def test_auto_detect_anthropic_from_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test_key"
        self.assertEqual(get_provider(), "anthropic")
        self.assertEqual(resolve_base_url(), "https://api.anthropic.com/v1")
        self.assertEqual(resolve_model(), "claude-3-5-sonnet-20241022")
        self.assertEqual(resolve_api_key(), "sk-ant-test_key")

    @_clean_env
    def test_auto_detect_openai_from_key(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-openai_test_key"
        self.assertEqual(get_provider(), "openai")
        self.assertEqual(resolve_base_url(), "https://api.openai.com/v1")
        self.assertEqual(resolve_model(), "gpt-4o-mini")
        self.assertEqual(resolve_api_key(), "sk-proj-openai_test_key")

    @_clean_env
    def test_auto_detect_deepseek_from_key(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-deepseek_test_key"
        self.assertEqual(get_provider(), "deepseek")
        self.assertEqual(resolve_base_url(), "https://api.deepseek.com/v1")
        self.assertEqual(resolve_model(), "deepseek-chat")
        self.assertEqual(resolve_api_key(), "sk-deepseek_test_key")

    @_clean_env
    def test_auto_detect_gemini_from_model(self):
        os.environ["API_KEY"] = "any-generic-key"
        os.environ["MODEL"] = "gemini-2.5-flash"
        self.assertEqual(get_provider(), "gemini")
        self.assertEqual(resolve_base_url(), "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(resolve_model(), "gemini-2.5-flash")
        self.assertEqual(resolve_api_key(), "any-generic-key")

    @_clean_env
    def test_auto_detect_anthropic_from_model(self):
        os.environ["API_KEY"] = "any-generic-key"
        os.environ["MODEL"] = "claude-3-7-sonnet"
        self.assertEqual(get_provider(), "anthropic")
        self.assertEqual(resolve_base_url(), "https://api.anthropic.com/v1")
        self.assertEqual(resolve_model(), "claude-3-7-sonnet")

    @_clean_env
    def test_auto_detect_openai_from_model(self):
        os.environ["API_KEY"] = "any-generic-key"
        os.environ["MODEL"] = "gpt-4o"
        self.assertEqual(get_provider(), "openai")
        self.assertEqual(resolve_base_url(), "https://api.openai.com/v1")
        self.assertEqual(resolve_model(), "gpt-4o")

    @_clean_env
    def test_generic_model_and_api_key_env_vars(self):
        os.environ["MODEL"] = "my-custom-model"
        os.environ["API_KEY"] = "my-custom-key"
        os.environ["BASE_URL"] = "http://localhost:8000/v1"
        self.assertEqual(resolve_model(), "my-custom-model")
        self.assertEqual(resolve_api_key(), "my-custom-key")
        self.assertEqual(resolve_base_url(), "http://localhost:8000/v1")

    @_clean_env
    def test_provider_specific_model_and_base_url_vars(self):
        os.environ["GEMINI_API_KEY"] = "key123"
        os.environ["GEMINI_MODEL"] = "gemini-1.5-pro"
        os.environ["GEMINI_BASE_URL"] = "https://custom-gemini-proxy.com/v1"
        self.assertEqual(resolve_model(), "gemini-1.5-pro")
        self.assertEqual(resolve_base_url(), "https://custom-gemini-proxy.com/v1")

    @_clean_env
    def test_provider_aliases(self):
        os.environ["PROVIDER"] = "google"
        self.assertEqual(get_provider(), "gemini")
        os.environ["PROVIDER"] = "claude"
        self.assertEqual(get_provider(), "anthropic")
        os.environ["PROVIDER"] = "grok"
        self.assertEqual(get_provider(), "xai")

    def test_detect_provider_from_key_shapes(self):
        self.assertEqual(detect_provider_from_key("gsk_123"), "groq")
        self.assertEqual(detect_provider_from_key("sk-ant-123"), "anthropic")
        self.assertEqual(detect_provider_from_key("AIzaSy123"), "gemini")
        self.assertEqual(detect_provider_from_key("sk-nous-123"), "nous")
        self.assertEqual(detect_provider_from_key("sk-or-123"), "openrouter")


class TestResponseNormalization(unittest.TestCase):
    """Verify that Anthropic, Gemini, and Ollama responses are normalized to OpenAI format."""

    def test_anthropic_response_normalized(self):
        anthropic_body = json.dumps({
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Anthropic answer here"}],
            "stop_reason": "end_turn",
        })
        normalized = _normalize_response_body(anthropic_body, "claude-3-5-sonnet")
        data = json.loads(normalized)
        self.assertIn("choices", data)
        self.assertEqual(data["choices"][0]["message"]["content"], "Anthropic answer here")
        self.assertEqual(data["choices"][0]["message"]["role"], "assistant")

    def test_gemini_response_normalized(self):
        gemini_body = json.dumps({
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Gemini answer here"}]
                    }
                }
            ]
        })
        normalized = _normalize_response_body(gemini_body, "gemini-2.0-flash")
        data = json.loads(normalized)
        self.assertIn("choices", data)
        self.assertEqual(data["choices"][0]["message"]["content"], "Gemini answer here")

    def test_ollama_response_normalized(self):
        ollama_body = json.dumps({
            "message": {"role": "assistant", "content": "Ollama answer here"}
        })
        normalized = _normalize_response_body(ollama_body, "llama3.2")
        data = json.loads(normalized)
        self.assertIn("choices", data)
        self.assertEqual(data["choices"][0]["message"]["content"], "Ollama answer here")

    def test_openai_response_untouched(self):
        openai_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OpenAI answer"}}]
        })
        normalized = _normalize_response_body(openai_body, "gpt-4o")
        self.assertEqual(normalized, openai_body)


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
        self.assertEqual(len(anthropic_payload["messages"]), 1)
        self.assertEqual(anthropic_payload["messages"][0]["role"], "user")
        self.assertEqual(anthropic_payload["max_tokens"], 1024)


class TestLLMManagerCaching(unittest.TestCase):
    @_clean_env
    def test_manager_resolves_and_caches(self):
        m = LLMManager()
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "groq"
        self.assertEqual(m.provider, "groq")
        self.assertEqual(m.base_url, "https://api.groq.com/openai/v1")
        # Cached: changing env afterwards does not change the property
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "openai"
        self.assertEqual(m.base_url, "https://api.groq.com/openai/v1")
        # Until reset
        m.reset()
        self.assertEqual(m.base_url, "https://api.openai.com/v1")


class TestApiUtilsDelegation(unittest.TestCase):
    @_clean_env
    def test_get_base_url_delegates(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "together"
        self.assertEqual(get_base_url(), "https://api.together.xyz/v1")

    @_clean_env
    def test_get_model_delegates(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "together"
        self.assertEqual(get_model(), "meta-llama/Llama-3.3-70B-Instruct-Turbo")

    @_clean_env
    def test_load_key_delegates_to_provider_env(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "sk-openai-key"
        self.assertEqual(load_nous_api_key(), "sk-openai-key")


if __name__ == "__main__":
    unittest.main()
