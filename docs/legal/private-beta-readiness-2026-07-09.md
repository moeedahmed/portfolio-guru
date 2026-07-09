# Portfolio Guru Private Beta Legal Readiness

Date: 2026-07-09
Status: internal product/legal-readiness note, not legal advice.
Commercial route: Solvoro Labs (US).
Product page: https://emgurus.com/portfolio

## Conclusion

A small, controlled private beta can continue if the user experience keeps
explicit consent, draft-only Kaizen saves, easy withdrawal/reset, and no public
or paid-launch claims.

Wider public launch or paid launch should stay blocked until the draft legal
documents are finalised and the remaining review markers are closed.

## Source-Checked Legal Direction

Official ICO guidance checked on 2026-07-09:

- Special category data can be processed where the data subject has given
  explicit consent for specified purposes, but the consent must be specific,
  informed, affirmative, and withdrawable.
- ICO consent guidance requires consent to be clear, granular, prominent,
  separate from other terms where appropriate, easy to withdraw, and recorded.
- ICO data-protection-fee guidance says organisations using personal
  information generally need to pay the data protection fee unless an exemption
  applies.

Sources:

- https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/special-category-data/what-are-the-conditions-for-processing/
- https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/lawful-basis/consent/what-is-valid-consent/
- https://ico.org.uk/for-organisations/data-protection-fee/

Repo status:

- `docs/legal/consent-copy.md` already contains a strong private-beta consent
  shape: health data, no patient identifiers, draft-only Kaizen save, encrypted
  Kaizen credentials, subprocessors, withdrawal/reset.
- `docs/legal/privacy-policy.md` and `docs/legal/terms-of-service.md` remain
  draft-only and contain review markers. They are not ready to be treated as
  public in-force legal documents.

## Private Beta Boundary

Allowed for the current Telegram-first beta:

- 3-5 trusted doctors invited directly.
- Clear beta framing before use.
- Explicit consent before first case.
- Draft-only Kaizen save; user reviews/submits manually in Kaizen.
- No public launch, no paid plan enforcement, no broad marketing claim.
- Admin can track PHI-free funnel and filing reliability only.

Required before re-engaging testers:

- Keep consent gate active.
- Make sure `/privacy` and setup copy do not claim final legal completeness.
- Make withdrawal/reset route clear.
- Do not ask testers to include patient identifiers.

## Wider/Public/Paid Launch Blockers

Resolve before public or paid launch:

1. Confirm final controller identity and contact route under Solvoro Labs (US).
2. Decide UK GDPR representative / ICO fee / exemption position with proper
   legal/accountability review.
3. Close all review markers in privacy policy and terms.
4. Confirm processor/subprocessor list, regions, retention, and deletion route.
5. Confirm Kaizen automation terms and acceptable-use posture.
6. Confirm Stripe/payment terms, refund/cancellation language, and tax/VAT route.
7. Confirm clinical safety wording: draft assistance only, no RCEM endorsement,
   no guaranteed ARCP outcome, no final clinical/professional judgement.

## Practical Decision

Do not wait for a full legal pack to keep a tiny private beta moving. Do not
launch publicly or charge users until the legal pack is no longer draft-only.
