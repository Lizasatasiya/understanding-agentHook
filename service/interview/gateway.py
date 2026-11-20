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
from service.trace import trace


class ModelGateway:
    summarizer_cls = ChangeSummary
    generator_cls = QuestionGenerator
    evaluator_cls = AnswerEvaluator

    def summarize(self, context: dict) -> dict:
        changes = (context or {}).get("structured_changes") or []
        stage_input = [
            {"file": item.get("file"), "function": item.get("function")}
            for item in changes[:8]
            if isinstance(item, dict)
        ]
        summary = self.summarizer_cls().generate(context or {})
        summary = summary if isinstance(summary, dict) else {}
        trace("model.summarize", source="ChangeSummary.generate", input=stage_input, output=summary)
        return summary

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
            trace("model.questions", source="run_master_analysis", repository=env["repository"], branch=env["branch"])
            analysis = run_master_analysis(
                context, summary, hints=hints, env=env, question_generator=generator,
            )
        if analysis and analysis.get("questions"):
            raw = generator.validate(analysis["questions"])
            findings = list(analysis.get("findings") or [])
            source = "run_master_analysis"
        else:
            trace("model.questions", source="QuestionGenerator.generate", repository=env["repository"], branch=env["branch"])
            raw = generator.validate(generator.generate(context, summary, hints, env=env))
            source = "QuestionGenerator.generate"
        fresh = []
        for index, question in enumerate(raw or [], 1):
            if not isinstance(question, dict) or question.get("is_fallback"):
                continue
            question = dict(question)
            question["question_id"] = question.get("question_id") or f"q{index}"
            fresh.append(question)
        trace(
            "model.questions.done",
            source=source,
            input={"what_changed": (summary or {}).get("what_changed") or "", "impact": (summary or {}).get("impact") or ""},
            output=[
                {"id": question.get("question_id"), "type": question.get("type"), "question": question.get("question") or ""}
                for question in fresh
            ],
        )
        return {"questions": fresh, "findings": findings}

    def assess(self, question: dict, answer_text: str, context: dict, summary: dict):
        result = self.evaluator_cls().evaluate(
            question,
            {"answer": answer_text or "", "status": "answered"},
            context,
            summary,
        )
        trace(
            "model.assess.done",
            source="AnswerEvaluator.evaluate",
            input={"question_id": question.get("question_id"), "question": question.get("question") or "", "answer": answer_text or ""},
            output={
                "score": result.score,
                "evaluation": result.evaluation,
                "missing": result.missing_concepts,
                "covered": result.covered_concepts,
                "llm_verified": result.llm_verified,
            },
        )
        return result
