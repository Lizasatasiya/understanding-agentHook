"""Sequences the model steps for one interview.

Summary, then questions, then one score per answer. The defense state
machine calls this gateway. The hook does not.
"""

from service.interview.intelligence import (
    master_analysis_enabled,
    run_master_analysis,
)
from service.interview.questions import QuestionGenerator
from service.interview.scoring import AnswerEvaluator
from service.interview.summary import ChangeSummary


class ModelGateway:
    summarizer_cls = ChangeSummary
    generator_cls = QuestionGenerator
    evaluator_cls = AnswerEvaluator

    def summarize(self, context: dict) -> dict:
        summary = self.summarizer_cls().generate(context or {})
        return summary if isinstance(summary, dict) else {}

    def questions(self, payload: dict, summary: dict) -> dict:
        """Summary is already written. Next the model writes the questions.

        Optional master analysis runs first when enabled. Fallback questions
        are dropped. Findings are advisory and come back with the questions.
        """
        context = payload.get("context") or {}
        hints = payload.get("hints") or {}
        env = {
            "repository": payload.get("repository") or "unknown",
            "branch": payload.get("branch") or "unknown",
        }
        generator = self.generator_cls()
        analysis = None
        findings = []
        if master_analysis_enabled():
            analysis = run_master_analysis(
                context, summary, hints=hints, env=env, question_generator=generator,
            )
        if analysis and analysis.get("questions"):
            raw = generator.validate(analysis["questions"])
            findings = list(analysis.get("findings") or [])
        else:
            raw = generator.validate(generator.generate(context, summary, hints, env=env))
        fresh = []
        for index, question in enumerate(raw or [], 1):
            if not isinstance(question, dict) or question.get("is_fallback"):
                continue
            question = dict(question)
            question["question_id"] = question.get("question_id") or f"q{index}"
            fresh.append(question)
        return {"questions": fresh, "findings": findings}

    def assess(self, question: dict, answer_text: str, context: dict, summary: dict):
        return self.evaluator_cls().evaluate(
            question,
            {"answer": answer_text or "", "status": "answered"},
            context,
            summary,
        )
