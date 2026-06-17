# 90-Second Demo Script — Portfolio Guru

Date: 2026-06-17
Owner: Founder / Portfolio Guru
Length target: 90 seconds. Tight. No fluff. Cuts are allowed but every
claim below must match the actual reality of the codebase on demo day.

Companion documents:

- [`HERO_CASE_2026-06-30.md`](HERO_CASE_2026-06-30.md) — synthetic case used.
- [`REHEARSAL_RUNBOOK.md`](REHEARSAL_RUNBOOK.md) — deterministic rehearsal path.
- [`../STRIPE_LOCAL_PROOF.md`](../STRIPE_LOCAL_PROOF.md) — Stripe proof scope.

## Honesty labels

- `[demo]` — pre-seeded synthetic data, no external call.
- `[test]` — deterministic in-process test backs this claim.
- `[manual]` — a human in the loop does this on the day.
- `[live-gated]` — runs only with founder credentials / approval.

If a beat is missing its label, it does not belong in the script.

## Beat sheet

### 0–10s — The problem `[demo]`

> "UK Emergency Medicine doctors keep a Royal College ePortfolio in
> Kaizen. Writing it up after a busy resus shift is the bit that gets
> dropped. Portfolio Guru turns the shift note into a structured
> Kaizen draft, and shows what evidence is still missing before
> ARCP."

On screen: `/portfolio` landing on EMGurus Hub. Pause on the safety
copy: draft-only, you approve, no patient identifiers, not RCEM
endorsed.

### 10–25s — Send a shift note in Telegram `[demo]`

> "Here's a realistic shift note — synthetic, no patient details — pasted
> into the Telegram bot."

On screen: paste the hero-case text from
[`HERO_CASE_2026-06-30.md`](HERO_CASE_2026-06-30.md). The bot replies
with a form recommendation. Tap `CBD`.

> "The bot recommends Case-Based Discussion. One tap."

### 25–45s — Draft preview `[demo]`

> "The bot returns a Kaizen-shaped draft: date, setting,
> presentation, reasoning, reflection, curriculum links. The
> learning-need field is blank because the trainee didn't record one
> — Portfolio Guru does not invent clinical content."

On screen: scroll the preview message. Linger on the blank
`learning_needs` field.

### 45–55s — Human approval `[manual]`

> "Nothing reaches Kaizen until the doctor taps Approve. The agent
> never submits, signs, sends, approves, rejects, or deletes on a
> supervisor's behalf. Draft-only is the hard line."

On screen: tap `Approve` in the Telegram preview.

### 55–65s — Kaizen draft save `[live-gated | demo]`

> "On the live path, a deterministic Playwright filer opens the
> Kaizen form in the doctor's own browser session and saves it as a
> draft. If anything looks off, we cut to the deterministic mocked
> fallback — same shape, no live external risk."

On screen, primary take: the live Kaizen draft confirmation. Fallback
take: the deterministic mocked filer's "draft saved" message. Narration
explicitly says when the fallback is in use.

### 65–75s — Dashboard and Portfolio Health `[demo]`

> "Back on the Hub dashboard, the case shows up. Portfolio Health
> flags the missing learning need as a gap. This is a directional
> planning aid before ARCP — not an official Royal College outcome."

On screen: `/portfolio/dashboard` then `/portfolio/health`. Linger on
the `learning need` gap.

### 75–85s — Stripe earn + agent ledger `[test | demo]`

> "Earning is a Stripe Unlimited subscription. The checkout, webhook,
> and tier-flip path is covered by deterministic in-process tests.
> Spend, operations, and safety are surfaced on the agent ledger —
> every figure labelled as a demo or test value, not a measured bill."

On screen: `/portfolio/ledger`. Point at the `Demo / Test` badges.

### 85–90s — Boundaries `[demo]`

> "No real-money assessor payouts. No supervisor autopilot. No
> public WhatsApp promise yet. EM + RCEM Kaizen first; everything
> else later."

On screen: hold on the safety guardrails section of the ledger.

## What the script never says

The script must never claim or imply, in narration or on-screen text:

- A public WhatsApp v1 promise. WhatsApp remains a routed convenience
  for later.
- RCEM endorsement. Portfolio Guru is independent of the Royal College.
- A guaranteed ARCP / CESR / Portfolio Pathway outcome. Portfolio
  Health is directional.
- Auto-submit, auto-sign, auto-send, auto-approve, auto-reject, or
  auto-delete on a supervisor's behalf in Kaizen.
- Real assessor payouts. There is no real-money payout in this product.
- Live Stripe production charging. The proven path is test-mode and
  in-process.
- Multi-specialty support. v1 is UK EM + RCEM Kaizen.

If any beat above starts to drift toward one of these claims, cut it.

## Take order on demo day

1. Run [`REHEARSAL_RUNBOOK.md`](REHEARSAL_RUNBOOK.md) end-to-end once.
2. Record the primary take with the live Kaizen leg if all
   `[live-gated]` items are green.
3. Record a fallback take that uses the deterministic mocked Kaizen
   path. Keep both. If the primary take has any glitch, use the
   fallback; the narration explicitly allows that.
4. Do not edit the take to imply something the codebase did not do.
