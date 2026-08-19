# Review Output Contract

Use this contract for formal reviews and machine-readable integrations.

## Output language

- Write all human-readable review content in Vietnamese.
- Keep code identifiers, file paths, code snippets, Android API names, literal error messages, JSON keys, and enum values unchanged.
- In JSON output, write every descriptive value in Vietnamese, including `title`, `evidence`, `trigger`, `impact`, `recommendation`, `test_case`, summaries, regression analysis, required tests, missing context, verification, and assessment reasons.
- Do not mix English prose into the result unless preserving an official technical term prevents ambiguity.

## Severity

- `CRITICAL`: Exploitable severe security issue, unauthorized sensitive action, incorrect financial operation, unrecoverable data loss, or release failure for most users.
- `HIGH`: Likely crash/ANR in a core flow, major functional regression, serious lifecycle/race defect, broken upgrade, or broad user impact.
- `MEDIUM`: Credible defect in a realistic edge case, recoverable wrong state, significant localized performance issue, or materially incorrect error handling.
- `LOW`: Narrow risk or missing protection with a concrete path but limited impact.
- `INFO`: Concrete non-blocking improvement. Never use for generic style advice.

Base severity on impact and likelihood, not code complexity.

## Confidence

- `HIGH`: The available code proves a reachable path from trigger to failure.
- `MEDIUM`: Evidence is strong but one runtime contract or environmental fact remains unverified.
- `LOW`: Verification is needed. Prefer placing this in `missing_context` rather than presenting it as a defect.

## Finding gate

Include a finding only when all fields can be answered:

- Changed or newly exposed location.
- Concrete trigger.
- Reachable call/data path.
- Observable impact.
- Evidence not invalidated by guards or tests.
- Smallest safe recommendation or exact verification.

## JSON shape

Return strict JSON only when requested by an automated caller:

```json
{
  "summary": {
    "change_purpose": "",
    "behavior_before": "",
    "behavior_after": "",
    "overall_risk": "LOW | MEDIUM | HIGH | CRITICAL",
    "reviewed_range": "",
    "review_scope": [],
    "affected_modules": [],
    "affected_flows": []
  },
  "regression_analysis": [
    {
      "flow": "",
      "why_affected": "",
      "risk": "",
      "verification": ""
    }
  ],
  "findings": [
    {
      "title": "",
      "severity": "CRITICAL | HIGH | MEDIUM | LOW | INFO",
      "confidence": "HIGH | MEDIUM | LOW",
      "category": "CORRECTNESS | REGRESSION | BUSINESS_LOGIC | LIFECYCLE | CONCURRENCY | SECURITY | DATA | PERFORMANCE | COMPATIBILITY | UI_UX | TESTING | MAINTAINABILITY",
      "file": "",
      "line": "",
      "evidence": "",
      "trigger": "",
      "impact": "",
      "affected_flows": [],
      "recommendation": "",
      "test_case": ""
    }
  ],
  "required_tests": [
    {
      "priority": "HIGH | MEDIUM | LOW",
      "type": "UNIT | INTEGRATION | UI | MANUAL | MIGRATION | PERFORMANCE | SECURITY",
      "scenario": "",
      "expected_result": "",
      "related_files": []
    }
  ],
  "missing_context": [
    {
      "context": "",
      "reason": "",
      "how_to_verify": ""
    }
  ],
  "verification": {
    "checks_run": [],
    "checks_not_run": [],
    "residual_risk": ""
  },
  "final_assessment": {
    "decision": "APPROVE | APPROVE_WITH_SUGGESTIONS | REQUEST_CHANGES | BLOCK",
    "reason": "",
    "blocking_findings": []
  }
}
```

## Decision

- `APPROVE`: No evidence-backed defect; affected contracts and meaningful regressions were evaluated.
- `APPROVE_WITH_SUGGESTIONS`: Only concrete non-blocking LOW/INFO items remain.
- `REQUEST_CHANGES`: At least one credible defect should be corrected before merge.
- `BLOCK`: Critical security, financial-integrity, authorization, unrecoverable data-loss, or broad release-breaking risk exists.

Keep `findings` empty when no defect passes the finding gate. Put unknowns in `missing_context` and unexecuted validation in `verification.checks_not_run`.
