import unittest
from unittest.mock import patch, MagicMock
import os

from understanding_agent.api_utils import extract_json, load_groq_api_key, call_groq_api, load_nous_api_key, call_nous_api
from understanding_agent.answer_evaluator import AnswerEvaluator
from understanding_agent.server_client import ServerClient


class TestExtractJson(unittest.TestCase):
    """The most failure-prone code in the repo: LLM output parsing."""

    def test_plain_json_array(self):
        self.assertEqual(extract_json('[{"question": "Why?"}]'), [{"question": "Why?"}])

    def test_json_in_code_fence(self):
        text = 'Here are the questions:\n```json\n[{"question": "Why?"}]\n```\nThanks!'
        self.assertEqual(extract_json(text), [{"question": "Why?"}])

    def test_json_with_think_tags(self):
        text = '<think>reasoning here</think>[{"question": "Why?"}]'
        self.assertEqual(extract_json(text), [{"question": "Why?"}])

    def test_json_with_trailing_commas(self):
        self.assertEqual(extract_json('[{"a": 1,}]'), [{"a": 1}])

    def test_python_style_literals(self):
        self.assertEqual(extract_json("[{'a': True, 'b': None}]"), [{"a": True, "b": None}])

    def test_json_embedded_in_prose(self):
        text = 'Sure! Here you go: {"score": 85} hope that helps'
        self.assertEqual(extract_json(text), {"score": 85})

    def test_garbage_returns_none(self):
        self.assertIsNone(extract_json("no json here at all"))
        self.assertIsNone(extract_json(""))
        self.assertIsNone(extract_json(None))  # type: ignore[arg-type]


class TestApiRetry(unittest.TestCase):
    def test_retries_transient_errors_then_succeeds(self):
        responses = [(0, "connection refused"), (500, "server error"), (200, '{"ok": true}')]
        with patch("understanding_agent.api_utils._groq_request_once",
                   side_effect=[r for r in responses]) as mock_req, \
             patch("time.sleep"):
            status, body = call_groq_api("key", {"model": "m"})
        self.assertEqual(status, 200)
        self.assertEqual(mock_req.call_count, 3)

    def test_does_not_retry_client_errors(self):
        with patch("understanding_agent.api_utils._groq_request_once",
                   return_value=(401, "unauthorized")) as mock_req:
            status, _ = call_groq_api("key", {"model": "m"})
        self.assertEqual(status, 401)
        self.assertEqual(mock_req.call_count, 1)

    def test_returns_last_error_after_exhausting_retries(self):
        with patch("understanding_agent.api_utils._groq_request_once",
                   return_value=(0, "timeout")) as mock_req, \
             patch("time.sleep"):
            status, body = call_groq_api("key", {"model": "m"}, retries=2)
        self.assertEqual(status, 0)
        self.assertEqual(mock_req.call_count, 3)
        self.assertEqual(body, "timeout")

    def test_no_key_short_circuits(self):
        status, body = call_groq_api("", {"model": "m"})
        self.assertEqual(status, 0)
        self.assertIn("GROQ_API_KEY", body)


class TestApiKeyLoading(unittest.TestCase):
    def test_nous_env_var_wins(self):
        with patch.dict(os.environ, {"NOUS_API_KEY": "from-nous-env"}):
            self.assertEqual(load_nous_api_key(), "from-nous-env")

    def test_env_var_wins(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "from-env"}):
            self.assertEqual(load_groq_api_key(), "from-env")

    def test_reads_env_file_in_cwd(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".env"), "w") as f:
                f.write('GROQ_API_KEY="from-file"\n')
            with patch("subprocess.check_output",
                       side_effect=Exception("not a git repo")), \
                 patch("os.path.abspath", lambda p: os.path.join(d, p)), \
                 patch("os.path.expanduser", lambda p: "/nonexistent-home"):
                # Patch getcwd too since abspath uses it
                with patch("os.getcwd", return_value=d):
                    self.assertEqual(load_groq_api_key(), "from-file")

    def test_never_reads_shell_configs(self):
        # Ensure ~/.zshrc etc. are never touched even if present
        with patch("subprocess.check_output",
                   side_effect=Exception("not a git repo")), \
             patch("os.getcwd", return_value="/nonexistent/dir"), \
             patch("os.path.expanduser", lambda p: "/tmp/fake-home") as exp:
            with patch("builtins.open", side_effect=FileNotFoundError()):
                result = load_groq_api_key()
        self.assertEqual(result, "")


class TestAnswerEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = AnswerEvaluator()
        self.q = {"question": "Why?", "type": "Reasoning",
                  "expected_concepts": ["x"], "evaluation_criteria": ["y"]}
        self.context = {"is_large_change": False, "structured_changes": []}

    def test_empty_answer_short_circuits_without_llm(self):
        with patch("understanding_agent.answer_evaluator.load_groq_api_key") as mock_key:
            res = self.evaluator.evaluate(
                self.q, {"answer": "", "status": "timeout"}, self.context, {})
        self.assertEqual(res.score, 0)
        mock_key.assert_not_called()

    def test_whitespace_answer_short_circuits(self):
        with patch("understanding_agent.answer_evaluator.load_groq_api_key") as mock_key:
            res = self.evaluator.evaluate(
                self.q, {"answer": "   ", "status": "answered"}, self.context, {})
        self.assertEqual(res.score, 0)
        mock_key.assert_not_called()

    def test_api_failure_fails_open_by_default(self):
        with patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value="k"), \
             patch.object(AnswerEvaluator, "_call_groq", return_value={}):
            res = self.evaluator.evaluate(
                self.q, {"answer": "my answer", "status": "answered"}, self.context, {})
        self.assertEqual(res.score, 76)
        self.assertIn("failing open", res.evaluation)

    def test_api_failure_blocks_in_closed_mode(self):
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_FAIL_MODE": "closed"}), \
             patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value="k"), \
             patch.object(AnswerEvaluator, "_call_groq", return_value={}):
            res = self.evaluator.evaluate(
                self.q, {"answer": "my answer", "status": "answered"}, self.context, {})
        self.assertEqual(res.score, 50)

    def test_no_key_fails_open_by_default(self):
        with patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value=""):
            res = self.evaluator.evaluate(
                self.q, {"answer": "my answer", "status": "answered"}, self.context, {})
        self.assertEqual(res.score, 76)

    def test_no_key_blocks_in_closed_mode(self):
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_FAIL_MODE": "closed"}), \
             patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value=""):
            res = self.evaluator.evaluate(
                self.q, {"answer": "my answer", "status": "answered"}, self.context, {})
        self.assertEqual(res.score, 0)
        self.assertIn("FAIL_MODE", res.evaluation)

    def test_successful_llm_result_passthrough(self):
        llm_result = {"score": 82, "evaluation": "good", "missing_concepts": ["locking"]}
        with patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value="k"), \
             patch.object(AnswerEvaluator, "_call_groq", return_value=llm_result):
            res = self.evaluator.evaluate(
                self.q, {"answer": "my answer", "status": "answered"}, self.context, {})
        self.assertEqual(res.score, 82)
        self.assertTrue(res.follow_up_required)
        self.assertEqual(res.missing_concepts, ["locking"])

    def test_fallback_evaluates_correct_answer_above_76(self):
        question = {
            "question": "What are the runtime risks of growing function length?",
            "expected_concepts": ["modular", "dependency injection", "cascading failure", "testability"],
            "evaluation_criteria": ["Mentions modularity", "Mentions cascading failure"]
        }
        good_answer = {
            "answer": "risk is that it is not modular and dependency injectable, causing cascading failure at runtime"
        }
        with patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value="k"), \
             patch.object(AnswerEvaluator, "_call_groq", return_value={}):
            res = self.evaluator.evaluate(question, good_answer, self.context, {})
        self.assertGreaterEqual(res.score, 85)
        self.assertIn("modular", res.covered_concepts)
        self.assertIn("cascading failure", res.covered_concepts)

    def test_fallback_evaluates_dismissive_answer_low(self):
        question = {
            "question": "Why?",
            "expected_concepts": ["modular", "coupling"]
        }
        with patch("understanding_agent.answer_evaluator.load_groq_api_key", return_value="k"), \
             patch.object(AnswerEvaluator, "_call_groq", return_value={}):
            res = self.evaluator.evaluate(question, {"answer": "idk"}, self.context, {})
        self.assertLessEqual(res.score, 15)
        self.assertTrue(res.follow_up_required)


class TestServerClient(unittest.TestCase):
    def test_sends_nothing_when_url_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_TELEMETRY_URL", None)
            with patch("urllib.request.urlopen") as mock_open:
                ServerClient().send({"some": "data"})
        mock_open.assert_not_called()

    def test_sends_when_url_set(self):
        payload = {"some": "data"}
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_TELEMETRY_URL": "http://x/test"}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.return_value.__enter__.return_value.status = 200
                ServerClient().send(payload)
        mock_open.assert_called_once()
        sent_req = mock_open.call_args[0][0]
        self.assertEqual(sent_req.full_url, "http://x/test")
        self.assertEqual(sent_req.get_method(), "POST")

    def test_failure_does_not_raise(self):
        import urllib.request
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_TELEMETRY_URL": "http://x/test"}):
            with patch("urllib.request.urlopen",
                       side_effect=Exception("connection refused")):
                # Must not raise
                ServerClient().send({"some": "data"})


class TestFollowUpGenerator(unittest.TestCase):
    def test_offline_fallback_question(self):
        from understanding_agent.followup_generator import FollowUpGenerator
        gen = FollowUpGenerator()
        result = gen.generate(
            {"question": "What happens on failure?"},
            {"answer": "it retries"},
            {"missing_concepts": ["max retry attempts"]}
        )
        self.assertTrue(len(result) > 0)
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
