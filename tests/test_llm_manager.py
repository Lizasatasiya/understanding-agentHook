import unittest
from unittest.mock import patch
import os

from understanding_agent.llm_manager import (
    PROVIDER_PRESETS, get_provider, resolve_base_url, resolve_model,
    resolve_api_key, LLMManager,
)
from understanding_agent.api_utils import get_base_url, get_model, load_nous_api_key


def _clean_env(f):
    """Run a test with all manager-relevant env vars cleared."""
    managed = (
        "UNDERSTANDING_AGENT_PROVIDER", "UNDERSTANDING_AGENT_MODEL",
        "UNDERSTANDING_AGENT_BASE_URL", "UNDERSTANDING_AGENT_API_KEY",
        "NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY",
        "NOUS_MODEL", "NOUS_BASE_URL", "GROQ_API_KEY", "GROQ_MODEL",
        "GROQ_BASE_URL", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
        "TOGETHER_API_KEY",
    )
    def wrapper(self):
        saved = {k: os.environ.get(k) for k in managed}
        for k in managed:
            os.environ.pop(k, None)
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
        for name in ("nous", "groq", "openai", "openrouter", "together", "ollama", "custom"):
            self.assertIn(name, PROVIDER_PRESETS)

    def test_groq_default_model_is_real_production_id(self):
        # Guard against the old typo qwen/qwen3.8-27b returning.
        self.assertEqual(
            PROVIDER_PRESETS["groq"]["model"], "llama-3.3-70b-versatile")


class TestProviderResolution(unittest.TestCase):
    @_clean_env
    def test_default_provider_is_nous(self):
        self.assertEqual(get_provider(), "nous")

    @_clean_env
    def test_provider_env_selects_preset(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "groq"
        self.assertEqual(get_provider(), "groq")
        self.assertEqual(resolve_base_url(), "https://api.groq.com/openai/v1")
        self.assertEqual(resolve_model(), "llama-3.3-70b-versatile")

    @_clean_env
    def test_unknown_provider_falls_back_to_nous(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "does-not-exist"
        self.assertEqual(get_provider(), "nous")

    @_clean_env
    def test_one_env_var_switches_provider_and_uses_preset_model(self):
        # The core promise: ONE var to switch platforms.
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "openai"
        self.assertEqual(resolve_base_url(), "https://api.openai.com/v1")
        self.assertEqual(resolve_model(), "gpt-4o-mini")

    @_clean_env
    def test_model_override_within_provider(self):
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "groq"
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "llama-3.1-8b-instant"
        self.assertEqual(resolve_model(), "llama-3.1-8b-instant")
        # base_url unchanged
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
        # Pre-manager users had a gsk_ key under a NOUS_* var.
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
        # Empty key is acceptable for local presets (no crash downstream).
        self.assertEqual(resolve_api_key(), "")


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
