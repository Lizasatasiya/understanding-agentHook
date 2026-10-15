"""Pokerface P1 — answer hygiene tests.

Contract: a transcription artifact must be flagged suspect BEFORE it can be
scored (a suspect answer that scores 0 aborts the commit — dictation failure
must never punish the developer). Mixed-language answers are NEVER suspect:
they are legitimate and the evaluator judges concept coverage, not language.
"""

import os
import unittest

from understanding_agent.answer_hygiene import (
    classify_answer,
    hygiene_enabled,
    script_counts,
)


class TestClassifyAnswer(unittest.TestCase):
    def test_empty_is_suspect(self):
        self.assertEqual(classify_answer("")[0], True)
        self.assertEqual(classify_answer("   \n\t ")[0], True)

    def test_single_clipped_token_is_suspect(self):
        suspect, reason = classify_answer("the")
        self.assertTrue(suspect)
        self.assertEqual(reason, "single-token")

    def test_asr_stutter_is_suspect(self):
        suspect, reason = classify_answer("the the the the the")
        self.assertTrue(suspect)
        self.assertIn("repetition", reason)

    def test_normal_answer_is_not_suspect(self):
        suspect, _ = classify_answer(
            "The function validates the token and returns early on failure"
        )
        self.assertFalse(suspect)

    def test_mixed_language_answer_is_never_suspect(self):
        # Hinglish-style bilingual answer: legitimate, must score normally
        suspect, _ = classify_answer(
            "yeh function token ko validate karta hai aur fail hone par early return karta hai"
        )
        self.assertFalse(suspect)

    def test_mixed_script_answer_is_not_suspect(self):
        # Devanagari + Latin in one answer: still a real answer
        suspect, _ = classify_answer(
            "यह function token validate करता है and returns early on failure"
        )
        self.assertFalse(suspect)

    def test_short_two_word_answer_is_not_suspect(self):
        # A terse-but-real answer (2 tokens) passes — only ONE token is suspect
        suspect, _ = classify_answer("validates tokens")
        self.assertFalse(suspect)

    def test_repetition_below_threshold_is_not_suspect(self):
        # 'the' repeated but under 60% dominance: real sentence, not stutter
        suspect, _ = classify_answer("the handler the model the route and the view bind")
        self.assertFalse(suspect)


class TestScriptCounts(unittest.TestCase):
    def test_latin_only(self):
        counts = script_counts("hello world")
        self.assertEqual(counts, {"latin": 10})

    def test_mixed_devanagari_latin(self):
        counts = script_counts("यह test है")
        self.assertEqual(counts.get("devanagari"), 3)
        self.assertEqual(counts.get("latin"), 4)

    def test_cjk_detection(self):
        counts = script_counts("テスト test")
        self.assertGreater(counts.get("cjk", 0), 0)
        self.assertGreater(counts.get("latin", 0), 0)

    def test_digits_and_punctuation_ignored(self):
        self.assertEqual(script_counts("123 !!! ..."), {})


class TestHygieneKillSwitch(unittest.TestCase):
    def test_enabled_by_default(self):
        os.environ.pop("UNDERSTANDING_AGENT_ANSWER_HYGIENE", None)
        self.assertTrue(hygiene_enabled())

    def test_disabled_by_env(self):
        os.environ["UNDERSTANDING_AGENT_ANSWER_HYGIENE"] = "off"
        try:
            self.assertFalse(hygiene_enabled())
        finally:
            del os.environ["UNDERSTANDING_AGENT_ANSWER_HYGIENE"]


if __name__ == "__main__":
    unittest.main()
