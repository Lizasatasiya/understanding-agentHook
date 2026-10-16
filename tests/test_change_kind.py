"""Bad Romance item 3 — deterministic ChangeKind tier: frozen test matrix.

Every category fixture asserts the classifier's dominant kind and (where the
change is pure) that the category is isolated. The matrix is FROZEN: these
fixtures define the classifier's contract, like the gate matrix in
tests/test_security_gate.py defines the gate's.
"""

import unittest

from understanding_agent.change_kind import (
    DOMAINS,
    classification_hint_lines,
    classify_change,
)


def _change(paths=None, changed_functions=None, added_lines=""):
    """Build a minimal changes/context pair like the detector produces."""
    files = []
    for p in (paths or []):
        entry = {"path": p, "status": "modified"}
        if changed_functions and p == (paths or [None])[0]:
            entry["changed_functions"] = [
                {"name": n, "change_type": "modified", "added_calls": []}
                for n in changed_functions
            ]
        files.append(entry)
    context = {"structured_changes": [{"file": (paths or ["x"])[0],
                                       "function": "f", "diff": added_lines}]}
    return {"files": files}, context


class TestChangeKindMatrix(unittest.TestCase):
    def test_ui_component_change(self):
        changes, context = _change(
            paths=["src/components/CartRow.jsx"],
            changed_functions=["handleAdd"],
            added_lines="+  const handleAdd = () => {\n+    <Button onClick={handleAdd}>\n+    useState(1);",
        )
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "ui")
        self.assertGreater(ck["weights"]["ui"], 0.6)

    def test_data_model_migration(self):
        changes, context = _change(
            paths=["db/migrations/0007_add_orders.sql"],
            added_lines="+CREATE TABLE orders (\n+  FOREIGN KEY (user_id) REFERENCES users(id)\n+);",
        )
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "data_model")

    def test_control_flow_route_handler(self):
        changes, context = _change(
            paths=["src/api/checkout.py"],
            changed_functions=["handle_checkout"],
            added_lines="+def handle_checkout(request):\n+    if not request.user:\n+        raise HTTPError(401)",
        )
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "control_flow")
        self.assertEqual(ck["weights"]["ui"], 0.0)

    def test_infra_config_change(self):
        changes, context = _change(
            paths=["deploy/production.yaml"],
            added_lines="+replicas: 4\n+timeout_seconds: 30",
        )
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "infra_config")

    def test_tests_change(self):
        changes, context = _change(
            paths=["tests/test_checkout.py"],
            changed_functions=["test_checkout_rejects_expired_card"],
            added_lines="+def test_checkout_rejects_expired_card():\n+    assert result is False",
        )
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "tests")

    def test_docs_change(self):
        changes, context = _change(
            paths=["docs/architecture.md"],
            added_lines="+## Checkout flow\n+The service validates first.",
        )
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "docs")

    def test_mixed_ui_and_data_change_is_not_silent(self):
        # A mixed change must report BOTH strong categories, not collapse
        # into one — the question generator uses this to split focus.
        changes, context = _change(
            paths=["src/components/PaymentForm.tsx", "db/migrations/0009_cards.sql"],
            added_lines="+<CardInput onChange={this.onChange} />\n+ALTER TABLE cards ADD COLUMN token TEXT;",
        )
        ck = classify_change(changes, context)
        self.assertGreaterEqual(ck["weights"]["ui"], 0.25)
        self.assertGreaterEqual(ck["weights"]["data_model"], 0.25)
        self.assertTrue(ck["dominant"] in ("ui", "data_model"))

    def test_unknown_when_no_signals(self):
        changes, context = _change(paths=["tool.py"], added_lines="+x = 1")
        ck = classify_change(changes, context)
        self.assertEqual(ck["dominant"], "control_flow")  # weak .py path signal only
        # a truly empty change is unknown, never an exception
        empty = classify_change({"files": []}, {"structured_changes": []})
        self.assertEqual(empty["dominant"], "")
        self.assertEqual(sum(empty["weights"].values()), 0.0)

    def test_weights_are_normalized_and_typed(self):
        changes, context = _change(
            paths=["src/components/CartRow.jsx"],
            added_lines="+<Button />",
        )
        ck = classify_change(changes, context)
        self.assertEqual(sorted(ck["weights"].keys()), sorted(DOMAINS))
        self.assertAlmostEqual(sum(ck["weights"].values()), 1.0, places=1)
        self.assertIsInstance(ck["signals"], list)

    def test_hint_lines_guide_questions_and_handle_unknown(self):
        ck = classify_change({"files": [{"path": "src/components/A.jsx"}]},
                             {"structured_changes": []})
        lines = classification_hint_lines(ck)
        self.assertTrue(any("CHANGE CLASSIFICATION" in l for l in lines))
        self.assertTrue(any("question focus" in l for l in lines))
        # unknown → no lines, callers proceed without the section
        self.assertEqual(classification_hint_lines({"dominant": ""}), [])
        self.assertEqual(classification_hint_lines(None), [])


if __name__ == "__main__":
    unittest.main()
