from typing import List, Dict, Any, Optional

class EvaluationResult:
    def __init__(self, data: dict):
        self.score = data.get("score", 0)
        self.technical_correctness = data.get("technical_correctness", 0)
        self.code_understanding = data.get("code_understanding", 0)
        self.reasoning = data.get("reasoning", 0)
        self.specificity = data.get("specificity", 0)
        self.covered_concepts = data.get("covered_concepts", [])
        self.missing_concepts = data.get("missing_concepts", [])
        self.incorrect_claims = data.get("incorrect_claims", [])
        self.evaluation = data.get("evaluation", "")
        self.confidence = data.get("confidence", 0.0)
        
        # If there are missing concepts, or the prompt explicitly asked for follow up
        self.follow_up_required = bool(data.get("follow_up_required", len(self.missing_concepts) > 0))

    def to_dict(self):
        return {
            "score": self.score,
            "technical_correctness": self.technical_correctness,
            "code_understanding": self.code_understanding,
            "reasoning": self.reasoning,
            "specificity": self.specificity,
            "covered_concepts": self.covered_concepts,
            "missing_concepts": self.missing_concepts,
            "incorrect_claims": self.incorrect_claims,
            "evaluation": self.evaluation,
            "confidence": self.confidence,
            "follow_up_required": self.follow_up_required
        }
