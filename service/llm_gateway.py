"""The only service module that calls a model.

Summary, question generation, and answer assessment stay behind this gateway
so the interview flow can be reordered without the hook knowing about models.
"""

from understanding_agent.context.change_intelligence import (
    master_analysis_enabled,
    run_master_analysis,
)
from understanding_agent.context.change_summary import ChangeSummary
from understanding_agent.evaluation.answer_evaluator import AnswerEvaluator
from understanding_agent.questions.question_generator import QuestionGenerator


class ModelGateway:
    summarizer_cls = ChangeSummary
    generator_cls = QuestionGenerator
    evaluator_cls = AnswerEvaluator

    def summarize(self, context: dict) -> dict:
        summary = self.summarizer_cls().generate(context or {})
        return summary if isinstance(summary, dict) else {}

    def questions(self, payload: dict, summary: dict) -> list:
        context = payload.get("context") or {}
        hints = payload.get("hints") or {}
        env = {
            "repository": payload.get("repository") or "unknown",
            "branch": payload.get("branch") or "unknown",
        }
        generator = self.generator_cls()
        analysis = None
        if master_analysis_enabled():
            analysis = run_master_analysis(
                context, summary, hints=hints, env=env, question_generator=generator,
            )
        if analysis and analysis.get("questions"):
            raw = generator.validate(analysis["questions"])
        else:
            raw = generator.validate(generator.generate(context, summary, hints, env=env))
        fresh = []
        for index, question in enumerate(raw or [], 1):
            if not isinstance(question, dict) or question.get("is_fallback"):
                continue
            question = dict(question)
            question["question_id"] = question.get("question_id") or f"q{index}"
            fresh.append(question)
        return fresh

    def assess(self, question: dict, answer_text: str, context: dict, summary: dict):
        return self.evaluator_cls().evaluate(
            question,
            {"answer": answer_text or "", "status": "answered"},
            context,
            summary,
        )
