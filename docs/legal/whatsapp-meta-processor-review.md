> **DRAFT - NOT YET IN FORCE.** This is a working note for founder and
> solicitor / DPO review. It is not legal advice and must not be treated as
> approval to launch WhatsApp.

# WhatsApp / Meta Processor Review Note

Date: 2026-07-07
Status: WhatsApp is blocked for tester rollout until this review is completed
and signed off.

## Launch Decision Under Review

Portfolio Guru should not route testers through the general EMGurus WhatsApp
account. The rollout path is a dedicated Portfolio Guru WhatsApp number,
account, and Hermes profile. Hermes/WhatsApp is only a channel shell; the
Portfolio Guru deterministic engine remains the product brain.

The current `portfolio-guru` Hermes WhatsApp credentials must not be started if
they are linked to the same underlying WhatsApp account as EMGurus.

## Primary Sources Checked

Official WhatsApp/Meta sources reviewed on 2026-07-07:

- WhatsApp Business Terms of Service, last updated 2024-02-16:
  https://www.whatsapp.com/legal/business-terms/
- WhatsApp Business Data Processing Terms, last updated 2025-08-22:
  https://www.whatsapp.com/legal/business-data-processing-terms/
- WhatsApp Business Messaging Policy:
  https://whatsappbusiness.com/policy/

Key review implications from those sources:

- WhatsApp Business Terms require a valid business account and put legal,
  privacy, security, and consent responsibilities on the company using the
  service.
- The Business Terms incorporate the WhatsApp Business Data Processing Terms
  where WhatsApp processes Customer Data as processor.
- The Data Processing Terms describe WhatsApp as processing Customer Data on the
  controller's instructions where they apply, and refer to data transfer
  addenda for cross-border transfers.
- The Business Messaging Policy requires opt-in, honoring opt-out/block
  requests, and a clear support/escalation route when automation is used.
- The Business Messaging Policy contains health-information and regulated
  vertical caveats that must be assessed for a UK clinical portfolio tool,
  especially because Portfolio Guru processes special-category health data.

## Processor Classification To Confirm

Legal must confirm the exact WhatsApp route before launch:

- WhatsApp Business App, WhatsApp Business Platform / Cloud API, or a Hermes
  browser-profile wrapper.
- Contracting entity for the controller's location.
- Whether the WhatsApp Business Data Processing Terms apply to the chosen route.
- Whether any additional provider, BSP, hosting, device, or browser-session
  operator is also a processor/sub-processor.

Working assumption for the existing legal drafts: Meta / WhatsApp is a future
messaging transport processor receiving message content in transit and a
WhatsApp identifier. That assumption remains unapproved until this note is
closed.

## Review Checklist

Before WhatsApp tester rollout, record decisions for each item:

| Item | Decision / Evidence |
| --- | --- |
| Dedicated Portfolio Guru number/account exists and is distinct from EMGurus | `REVIEW` |
| Chosen route: Business App vs Platform/Cloud API vs other | `REVIEW` |
| Contracting WhatsApp/Meta entity | `REVIEW` |
| Data Processing Terms / DPA status | `REVIEW` |
| International transfer mechanism: UK IDTA, SCCs + UK Addendum, or other | `REVIEW` |
| Transfer Risk Assessment needed/completed | `REVIEW` |
| Health/special-category policy fit for portfolio case content | `REVIEW` |
| User opt-in and opt-out wording | `REVIEW` |
| Human support/escalation route | `REVIEW` |
| Privacy Policy, Terms, consent copy, DPIA, and ROPA updates needed | `REVIEW` |
| Retention and deletion behavior for WhatsApp-originated messages/media | `REVIEW` |
| Security controls for profile/device/session access | `REVIEW` |
| Incident response path for WhatsApp account compromise | `REVIEW` |

## Required Legal Updates Before Launch

At minimum, review and update these files if the decisions above change their
current draft wording:

- `docs/legal/privacy-policy.md`
- `docs/legal/terms-of-service.md`
- `docs/legal/consent-copy.md`
- `docs/legal/processors-ropa.md`
- `docs/legal/dpia.md`

The current legal set already marks Meta/WhatsApp as future and not yet
reviewed. That marker should remain a launch blocker until the completed review
names the exact route, transfer mechanism, user notices, and operational
controls.

## Operational Constraints For Legal Sign-off

- No group/community WhatsApp filing.
- No marketing broadcast to testers unless opt-in and template rules are
  separately reviewed.
- No collection of patient identifiers by design; users remain instructed to
  anonymise.
- No clinical or medical advice.
- No supervisor submission/signing/approval.
- Draft-only Kaizen filing after explicit user approval remains the hard safety
  boundary.

## Approval Record

| Role | Name | Date | Decision |
| --- | --- | --- | --- |
| Founder / accountable owner | `REVIEW` | `REVIEW` | `REVIEW` |
| Solicitor / DPO | `REVIEW` | `REVIEW` | `REVIEW` |

Launch remains blocked until the decision row above is completed and the
readiness guard has been run with the explicit legal approval environment
variable.
