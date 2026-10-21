# Quick Demo — "The Vibe-Coded Checkout" (90 seconds)

One story, one repo, two defenses. Every step below was executed and
verified on this machine. The `shopcart/` repo is a throwaway demo repo —
never push it.

## The story you're telling

> "A developer accepts an AI-generated change to the checkout pricing
> module. **Tests pass. CI would be green.** It contains two things every
> vibe-coded commit hides: a live payment key, and a business rule the AI
> silently broke. Watch the hook catch both — one deterministically, one by
> making the author prove they understand the code."

The planted bug (for your pocket, never say it up front): `stack_coupons`
checks the `MIN_TOTAL` floor **after** applying a coupon, so the last
coupon can push the total below the floor. The AI's own tests stay green.
The oral-defense question calls out this exact edge case — that's the
payoff moment.

## T-15 min setup (once, in your terminal)

```bash
sh ~/understanding-agentHook/demo/QUICK_DEMO/reset.sh   # demo-ready state
cd ~/understanding-agentHook/demo/QUICK_DEMO/shopcart
git status --short        # 3 staged files: payments.py, pricing.py, tests
```

Already warm on this machine: pre-commit hook env, `.env` (Nous key +
`deepseek/deepseek-v4-flash` pinned — benched ~25s for 3 questions).

## The 90-second script

### 0:00–0:10 — the setup (terminal 1)

```bash
git diff --cached --stat
```

Say: *"An AI-generated checkout change — coupon stacking plus a payments
integration. Tests pass. Let's commit it."*

### 0:10–0:25 — Beat 1: the credential gate (deterministic, zero AI)

```bash
git commit -m "feat: add coupon stacking and payment integration"
```

> **Blocked in under 1 second** — *"Commit blocked: credential detected in
> staged code"* — `Stripe live secret key (***) in payments.py` — with the
> exact unstage command suggested.

Say: *"No AI anywhere in that decision — a regex-class credential gate.
The key never enters git history, and it cost zero API calls."*

### 0:25–1:05 — Beat 2: the oral defense

```bash
git restore --staged payments.py
python3 -c "import sys; sys.path.insert(0,'.'); from pricing import stack_coupons; print(stack_coupons(100.0, [80, 80]))"   # → 4.0, below the 5.0 floor — tests stayed green
git commit -m "feat: add coupon stacking"
```

While questions generate (~25s — narrate over it: *"first a deterministic
change classification and a 15-check standards audit run — free, no LLM
yet — then the LLM reads only the actual diff"*), the room sees:

- `Change classification: tests (control_flow …) — signals: …`
- `Coding Standards Audit` passing
- 3 questions aimed at **this** diff (captured live on this machine):
  1. *What happens if the total drops below MIN_TOTAL during coupon application?* ← the planted bug
  2. *How does the function handle an empty coupons list?*
  3. *What error is raised when more than MAX_COUPONS are passed?*

Answer in your own words (type, or press **[Tab]** and speak — it
transcribes live). Cheat-sheet answers:

1. *"The break happens AFTER applying, so the last coupon still lands and
   the total can end below the 5.0 floor — 100 → 80% → 20 → 80% → 4.0.
   The floor is checked one coupon too late."*
2. *"Stacking is a no-op — the loop never runs, total returned unchanged."*
3. *"ValueError — 'at most 3 coupons per order' — before any coupon is applied."*

### 1:05–1:20 — the payoff

Score per answer appears (`Correctness / Understanding / Reasoning /
Specificity`), average **> 75% → commit lands**.

Say the closer: *"The credential gate was deterministic. The defense was
AI. And the very first question was the one edge case the AI got wrong —
the 5-dollar floor — which its own tests missed. The author didn't just
ship code; they proved — out loud — that they understood it. And for
teams, every defense session lands in a central ledger CI can check:
a commit with no defense record is unaudited by definition."*

## Optional +15s beat — the fail path

Answer any question vaguely → score < 75% → **commit aborted** (attempt
saved). Re-run the commit: same-diff retry reuses cached questions, and
you get a targeted follow-up on the concept you missed. Shows the hook
isn't theater — it actually blocks.

## Failure playbook (same as always)

| What breaks | What you say / do |
|---|---|
| Question gen > 30s | "gateway variance is documented" — keep talking; it lands |
| LLM unreachable | Hook fails OPEN: offline questions still appear, session marked `llm-unverified`, commit proceeds — "honest degradation, no key freeze" |
| Voice garbles | The hygiene layer re-asks; or just type |

## After the demo — reset for the next run

```bash
sh ~/understanding-agentHook/demo/QUICK_DEMO/reset.sh
```

## Repo layout (for you, not the room)

| Path | What it is |
|---|---|
| `shopcart/` | Throwaway git demo repo (never push) — base commit `demo-base` + staged AI change |
| `staged.patch` | The staged "AI-generated" change, frozen for instant resets |
| `shopcart/payments.py` | Hardcoded `sk_live_…` key (format-real, value fake) → triggers Beat 1 |
| `shopcart/pricing.py` (staged) | `stack_coupons` with the MIN_TOTAL floor bug → drives Beat 2 |
| `shopcart/.env` | gitignored — Nous key + `deepseek/deepseek-v4-flash` (fast: ~25s/3 questions vs ~95s default) |