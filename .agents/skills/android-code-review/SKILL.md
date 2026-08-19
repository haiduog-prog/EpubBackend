---
name: android-code-review
description: Review Android code changes for correctness, business-rule violations, regression risk, lifecycle and concurrency bugs, security, persistence compatibility, performance, and test coverage. Use for Android Merge Requests, Pull Requests, commit ranges, local diffs, patches, upgrade changes, or review plans involving Kotlin, Java, Jetpack Compose, XML Views, Coroutines/Flow, Room, WorkManager, AndroidManifest.xml, Gradle, SDKs, R8/ProGuard, JNI, or Android resources. Also use when asked whether an Android change can affect existing functions or is safe to merge.
---

# Android Code Review

Review behavioral impact, not merely syntax or style. Require a traceable path from the changed code to every reported failure.

## Preserve review scope

- Treat review and diagnosis requests as read-only. Do not edit code unless the user separately asks for fixes.
- Preserve user changes in a dirty worktree.
- Follow repository instructions such as `AGENTS.md` before inspecting the change.
- Use the user's stated base commit, branch, MR, PR, or range. Do not silently assume `main`.
- State the inspected range and any important context that was unavailable.

## Load references selectively

- Read [references/android-review-checklist.md](references/android-review-checklist.md) after identifying which Android surfaces changed. Apply only relevant sections.
- Read [references/output-contract.md](references/output-contract.md) when producing a formal review, machine-readable JSON, or severity/confidence classifications.
- For version-sensitive claims about Android releases, target SDK behavior, Gradle/AGP/Kotlin compatibility, Play requirements, or third-party SDKs, verify against current primary documentation. Distinguish sourced facts from inference.

## Use Vietnamese for every output

- Always write the complete review in Vietnamese, even when the source code, MR description, repository documentation, or user request is in another language.
- Keep file paths, class names, function names, variable names, code snippets, Android API names, error messages, JSON keys, and schema enum values unchanged.
- Explain quoted code and error messages in Vietnamese instead of translating their literal technical content.
- For structured JSON, keep the schema keys and enum values exactly as defined, but write every descriptive string and array item in Vietnamese.
- Do not mix English prose into headings, findings, evidence, impact, recommendations, test cases, missing context, verification, summaries, or final assessment unless the English text is a code identifier or an official technical term that would become ambiguous when translated.

## Review workflow

### 1. Resolve the change set

Inspect repository state before conclusions:

1. Read repository instructions.
2. Identify the requested baseline and head.
3. Inspect committed, staged, and unstaged changes that are in scope.
4. List changed files and classify them as behavior, API contract, UI/resource, persistence, build/configuration, manifest/security, native, or tests.
5. Include deleted and renamed files; do not review only added lines.

Prefer `rg` and Git-native diff commands. Avoid dumping very large generated files when targeted inspection is sufficient.

### 2. Build the project profile

Record only facts needed for the review:

- Application and affected modules.
- Architecture and state-management pattern actually used by the project.
- minSdk, compileSdk, targetSdk, Kotlin, AGP, Gradle, JDK, Compose, Room, and native-code presence when relevant.
- Product flavors, build types, feature flags, dependency injection, persistence, and navigation involved in the change.
- Business invariants stated in code, tests, MR text, or business documents.

Do not criticize the project merely for using Views instead of Compose, MVVM instead of MVI, manual DI instead of Hilt, or another valid established pattern.

### 3. Reconstruct behavior before and after

For every behavior-changing edit:

1. Read the complete changed function or class, not only the hunk.
2. Inspect declarations, interfaces, implementations, overrides, callers, and important downstream consumers.
3. Trace state, data, events, callbacks, storage, navigation, and side effects.
4. Compare accepted inputs, output values, execution order, default values, error behavior, and cancellation behavior before and after.
5. Identify contracts that must remain stable: nullability, enum/sealed branches, serialization fields, Intent extras, Parcelable data, resource names, cache keys, database schema, public SDK APIs, and cross-module interfaces.

Search narrowly first, then expand only when a dependency or contract makes another file relevant.

### 4. Perform regression analysis

Create an internal impact ledger for each meaningful change:

| Dimension | Question |
|---|---|
| Trigger | What input, event, lifecycle state, or platform condition reaches the new path? |
| Prior behavior | What did the same scenario do before? |
| New behavior | What does it do now? |
| Direct consumers | Which callers or screens depend on the changed contract? |
| Shared state | Which cache, singleton, database, Flow, event, or model can carry the effect elsewhere? |
| Failure mode | What observable crash, wrong state, data loss, security exposure, or user-visible defect results? |
| Verification | Which existing or new test proves the claim? |

Exercise only relevant scenarios, including success/error, null/empty/boundary input, duplicate action, concurrency, timeout, offline response, lifecycle recreation, background/foreground, process death, back navigation, deep link, upgrade with existing data, old/new supported API levels, flavors, locale, RTL, font scale, and large data.

For money, authentication, authorization, or irreversible operations, explicitly inspect rounding, units, idempotency, duplicate submission, stale account/user state, transaction boundaries, and sensitive-data exposure.

### 5. Apply Android-specific checks

Use the detailed checklist only for surfaces touched by the patch or reachable through its effects. Always consider:

- Kotlin/JVM correctness and API compatibility.
- Lifecycle, structured concurrency, cancellation, Flow collection, and event delivery.
- Compose recomposition/state or View/Fragment lifecycle, depending on the project.
- Room migrations, local data compatibility, cache invalidation, and network contracts.
- Android components, permissions, Intents, deep links, WebView, PendingIntent, and Manifest merging.
- Main-thread work, memory leaks, repeated I/O, bitmap/list scale, startup, battery, and background work.
- Gradle, R8/ProGuard, resources, flavors, ABI/native libraries, and SDK behavior changes.
- Accessibility, localization, window insets, adaptive layout, and state restoration when UI changes.

### 6. Validate every candidate finding

Before reporting a finding, require all of the following:

1. Point to the changed line or a line whose existing behavior is newly exposed by the change.
2. Show the concrete trigger and reachable call/data path.
3. Explain the observable impact.
4. Check nearby guards, lifecycle ownership, threading, defaults, and existing tests that may invalidate the claim.
5. Propose the smallest safe correction or an exact verification step.

Reject a candidate when it is only stylistic, speculative, unrelated to the change, already prevented by code, dependent on an implausible input, or a broad refactor preference. Put genuinely missing evidence in `missing_context`, not in findings.

Consolidate duplicate symptoms with one root cause. Do not repeat the same issue for every caller.

### 7. Assess tests

- Map each accepted finding to a reproducible test.
- Identify changed behavior that lacks coverage even when no defect is proven.
- Prefer unit tests for pure rules, coroutine/state tests for ordering and cancellation, migration tests for stored data, integration tests for module/API contracts, and UI/instrumentation tests for lifecycle or platform behavior.
- Specify setup, action, and expected result. Avoid vague requests such as “add more tests.”
- Run safe existing checks when the repository and task allow it. Report what ran and what could not run.

### 8. Produce the review

Lead with findings ordered by severity, then give a concise change/risk summary and required tests. Use exact file and line references when available.

Write all review prose in Vietnamese according to the mandatory output-language rules above.

Use the output contract reference for severity and confidence. If the caller requires structured output, emit valid JSON only. Otherwise use concise Markdown with:

1. Findings.
2. Regression impact.
3. Required tests.
4. Missing context or residual risk.
5. Final assessment.

Return an empty findings list when no evidence-backed defect exists. Never invent issues to make a review appear complete.

## Final decision rules

- Approve only after evaluating affected contracts and meaningful regression paths, not merely after finding no syntax errors.
- Use `APPROVE_WITH_SUGGESTIONS` only for non-blocking, concrete improvements.
- Use `REQUEST_CHANGES` for credible defects that should be corrected before merge.
- Use `BLOCK` for critical security, authorization, financial-integrity, unrecoverable data-loss, or broad release-breaking risk.
- State that review reduces risk rather than guarantees zero regression; identify the tests needed to close residual uncertainty.
