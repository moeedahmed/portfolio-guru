# Design: ARCP Health Feature (SOL-4)

## Overview
The ARCP Health feature provides trainees with real-time insight into their preparedness for their Annual Review of Competence Progression (ARCP). It leverages the existing portfolio filing data and analyzes it against RCEM curriculum requirements.

## Objectives
1.  Provide an at-a-glance dashboard view of ARCP readiness.
2.  Enable gap analysis against curriculum SLOs.
3.  Offer actionable suggestions to fulfill requirements.

## Architecture
-   **Backend:** Extends the existing `analyse_portfolio_health` function (currently used by `/health` in `bot.py`).
-   **API:** A new API endpoint (e.g., `/api/portfolio/health`) will be required for the web application to fetch this data.
-   **Frontend:** Consumes the API and renders charts (form distribution, SLO coverage) and lists (strengths, gaps, suggestions).

## Data Contract
The data contract follows the structure currently implemented in `backend/extractor.py`.

```json
{
  "total_cases": "integer",
  "form_distribution": {
    "<form_type>": "integer"
  },
  "slo_coverage": {
    "covered": ["<SLO_code>"],
    "gaps": ["<SLO_code>"]
  },
  "strengths": ["<string>"],
  "gaps": ["<string>"],
  "suggestions": ["<string>"],
  "arcp_readiness": "<on_track|needs_attention|at_risk>"
}
```

## Dashboard States
1.  **Empty/Loading:** Shown when no cases are filed yet or data is being fetched.
2.  **Ready:** Main view with summary metrics, ARCP readiness status, and SLO gap analysis.
3.  **Needs Attention/At Risk:** Same as Ready, but with higher visibility for suggested actions.

## First Implementation Slice
1.  Create `backend/api/portfolio_health.py` to wrap `analyse_portfolio_health` and handle authentication.
2.  Implement the GET endpoint `/api/portfolio/health` in the backend.
3.  Update the frontend dashboard to call this new endpoint and display the readiness status and case count.

## Implementation Plan
1.  **Backend:** Expose `analyse_portfolio_health` through a new web API endpoint.
2.  **Web App:** Implement the ARCP Health page in the dashboard.
3.  **Frontend:** Develop charts and visualization components.
