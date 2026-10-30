import unittest
import os
import json

from understanding_agent.llm_manager import (
    ORG_LLM_BASE_URL, ORG_LLM_MODEL, resolve_base_url, resolve_model,
    resolve_judge_model, resolve_api_key, LLMManager,
)
from understanding_agent.api_utils import (
    get_base_url, get_model, get_judge_model, load_nous_api_key,
    _normalize_response_body,
)


def _clean_env(f):
    """Run a test with model/key env vars cleared and .env ignored."""
    managed = (
        "UNDERSTANDING_AGENT_MODEL", "LLM_MODEL", "MODEL",
        "UNDERSTANDING_AGENT_API_KEY", "LLM_API_KEY", "API_KEY",
        "UNDERSTANDING_AGENT_BASE_URL", "LLM_BASE_URL", "BASE_URL",
        "UNDERSTANDING_AGENT_PROVIDER", "PROVIDER",
        "UNDERSTANDING_AGENT_JUDGE_MODEL",
        "UNDERSTANDING_AGENT_IGNORE_DOTENV",
        "NOUS_API_KEY", "NOUS_MODEL", "NOUS_BASE_URL",
        "GROQ_API_KEY", "GROQ_MODEL", "OPENAI_API_KEY",
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


class TestSingleModelConfig(unittest.TestCase):
    @_clean_env
    def test_default_is_org_gateway_and_default_model(self):
        self.assertEqual(resolve_base_url(), ORG_LLM_BASE_URL)
        self.assertEqual(resolve_base_url(), "http://llm.yorkdevs.link:2222/v1")
        self.assertEqual(resolve_model(), ORG_LLM_MODEL)
        self.assertEqual(resolve_judge_model(), resolve_model())

    @_clean_env
    def test_model_env_is_the_only_switch(self):
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "org-model-a"
        self.assertEqual(resolve_model(), "org-model-a")
        self.assertEqual(resolve_judge_model(), "org-model-a")
        self.assertEqual(get_model(), "org-model-a")
        self.assertEqual(get_judge_model(), "org-model-a")

    @_clean_env
    def test_generic_model_env_also_works(self):
        os.environ["MODEL"] = "org-model-b"
        self.assertEqual(resolve_model(), "org-model-b")

    @_clean_env
    def test_base_url_and_provider_env_cannot_redirect(self):
        os.environ["UNDERSTANDING_AGENT_BASE_URL"] = "https://api.groq.com/openai/v1"
        os.environ["UNDERSTANDING_AGENT_PROVIDER"] = "openai"
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "some-other-model"
        self.assertEqual(resolve_base_url(), "http://llm.yorkdevs.link:2222/v1")
        self.assertEqual(get_base_url(), "http://llm.yorkdevs.link:2222/v1")
        self.assertEqual(resolve_model(), "some-other-model")

    @_clean_env
    def test_judge_env_does_not_select_a_second_model(self):
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "shared-model"
        os.environ["UNDERSTANDING_AGENT_JUDGE_MODEL"] = "judge-only"
        self.assertEqual(resolve_judge_model(), "shared-model")
        self.assertEqual(get_judge_model(), get_model())

    @_clean_env
    def test_api_key_from_env(self):
        os.environ["UNDERSTANDING_AGENT_API_KEY"] = "org-key"
        os.environ["NOUS_API_KEY"] = "legacy-key"
        os.environ["GROQ_API_KEY"] = "groq-key"
        self.assertEqual(resolve_api_key(), "org-key")
        self.assertEqual(load_nous_api_key(), "org-key")

    @_clean_env
    def test_manager_caches_until_reset(self):
        m = LLMManager()
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "first"
        self.assertEqual(m.model, "first")
        self.assertEqual(m.base_url, "http://llm.yorkdevs.link:2222/v1")
        os.environ["UNDERSTANDING_AGENT_MODEL"] = "second"
        self.assertEqual(m.model, "first")
        m.reset()
        self.assertEqual(m.model, "second")


class TestResponseNormalization(unittest.TestCase):
    def test_openai_response_untouched(self):
        openai_body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "OpenAI answer"}}]
        })
        normalized = _normalize_response_body(openai_body, "gpt-4o")
        self.assertEqual(normalized, openai_body)

    def test_ollama_response_normalized(self):
        ollama_body = json.dumps({
            "message": {"role": "assistant", "content": "Ollama answer here"}
        })
        normalized = _normalize_response_body(ollama_body, "llama3.2")
        data = json.loads(normalized)
        self.assertEqual(data["choices"][0]["message"]["content"], "Ollama answer here")


if __name__ == "__main__":
    unittest.main()
