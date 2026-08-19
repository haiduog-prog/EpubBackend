# Android Review Checklist

Use this reference selectively. Do not turn every checklist item into a comment.

## Contents

1. Kotlin and contracts
2. Coroutines and Flow
3. Architecture and state
4. Jetpack Compose
5. Views, Activity, and Fragment
6. Persistence and cache
7. Network and serialization
8. Platform and security
9. Build, dependencies, and native code
10. Performance and resources
11. UI, accessibility, and localization
12. Regression test matrix

## 1. Kotlin and contracts

- Check unsafe `!!`, casts, platform types, nullable-to-non-null changes, and changed defaults.
- Check `==` versus `===`, string normalization, locale-sensitive casing, and regex boundaries.
- Check integer overflow, numeric conversion, `BigDecimal` scale/rounding, currency units, and percentage semantics.
- Check date/time source, timezone, clock injection, locale formatting, and boundary dates.
- Check mutable collections or objects escaping their owner and accidental aliasing after `copy`.
- Check data-class equality when instances are list items, cache keys, DiffUtil inputs, or Compose keys.
- Check enum/sealed exhaustiveness and unknown server values.
- Check changed visibility, overload resolution, default arguments, JVM signatures, Java callers, and binary/public API compatibility.
- Check reflection, annotations, generated code, KSP/KAPT, and serialization adapters affected by renames.

## 2. Coroutines and Flow

- Keep blocking I/O and CPU-heavy work off the main thread.
- Preserve structured concurrency; flag detached or nested launches that lose cancellation or errors.
- Match scope lifetime to work lifetime. Avoid UI work surviving its ViewModel or view lifecycle without intent.
- Check cancellation cleanup and callback-to-suspend cancellation.
- Check exception propagation, supervisor behavior, retry boundaries, and swallowed failures.
- Inspect races around shared mutable state, check-then-act logic, and simultaneous requests.
- Verify dispatcher choice and testability when dispatchers are hard-coded.
- Verify Flow hot/cold semantics, repeated collection, repeated network calls, and replay behavior.
- Check `stateIn`/`shareIn` scope, start policy, initial value, and stale state.
- Check `flowOn`, `catch`, `retry`, `combine`, `flatMapLatest`, debounce, and buffer for ordering or dropped work.
- Collect UI streams lifecycle-aware where required.
- Distinguish durable state from one-time effects; prevent effect replay after recreation.

## 3. Architecture and state

- Respect the project's established layer boundaries unless they create a concrete defect.
- Maintain a clear source of truth for screen and domain state.
- Verify loading, content, empty, partial, and error transitions.
- Check that `copy` updates retain unrelated state fields.
- Reset user-, account-, request-, or screen-specific state at the correct boundary.
- Check cache/singleton state when users, accounts, environments, or sessions switch.
- Avoid holding Activity, Fragment, View, or short-lived Context references in long-lived objects.
- Verify state restoration after configuration change or process death when the feature promises it.
- Check event ordering between ViewModel, reducer, middleware/use case, repository, and UI.

## 4. Jetpack Compose

- Do not perform uncontrolled side effects directly during composition.
- Verify keys for `remember`, `rememberSaveable`, `LaunchedEffect`, `DisposableEffect`, and `produceState`.
- Check stale captured lambdas/state; use updated state only where semantics require it.
- Prevent state writes after reads in the same composition path that can cause loops.
- Move expensive repeated calculations out of composition or memoize with correct keys.
- Give lazy items stable, unique keys when identity must survive moves or updates.
- Check unstable parameters and broad state reads only when they cause material recomposition.
- Collect Flow/LiveData with lifecycle-aware APIs appropriate to the project.
- Prevent navigation, toast, analytics, or network effects from replaying on recomposition.
- Check modifier order for size, clipping, drawing, gestures, semantics, and click area.
- Verify saveable state types and restoration keys.

## 5. Views, Activity, and Fragment

- Access view binding only within the view lifecycle.
- Remove listeners, callbacks, adapters, observers, and references when ownership ends.
- Avoid duplicate observer/listener registration after recreation.
- Check Fragment transactions after saved state and use of the correct FragmentManager.
- Verify Activity Result registration/launch timing and callback behavior.
- Check Intent flags, launch mode, task affinity, back stack, and result propagation.
- Verify RecyclerView identity, DiffUtil equality, stable IDs, positions, payloads, and restored scroll state.
- Prevent UI updates after the Activity/Fragment/view is inactive.
- Check WebView lifecycle, settings, bridge exposure, navigation, and cleanup.

## 6. Persistence and cache

- Require a Room migration for schema changes that affect installed users.
- Verify every supported upgrade path, not only the newest adjacent version.
- Preserve data across column/table rename, deletion, split, merge, or type/default changes.
- Treat destructive migration as data deletion and justify it explicitly.
- Store exported schemas and test migrations using real old schemas/data.
- Check query filtering, ordering, joins, null semantics, transactions, and conflict strategy.
- Preserve atomicity for multi-step writes and related remote/local updates.
- Check DataStore/SharedPreferences keys, defaults, type changes, encryption, and migration.
- Invalidate or namespace caches when source data, account, language, permission, or configuration changes.
- Check cache-key collision and normalization.

## 7. Network and serialization

- Verify endpoint, method, headers, authentication, request fields, and response mapping.
- Check absent/null/unknown fields and forward-compatible enum handling.
- Verify serialization annotations, obfuscation rules, reflection, generics, and date/number formats.
- Handle non-2xx responses, empty bodies, malformed data, timeout, cancellation, and offline state.
- Avoid blind retries for non-idempotent operations; use idempotency keys where the contract supports them.
- Prevent duplicate payment, transfer, submission, or mutation from repeated taps or retries.
- Check pagination boundaries, ordering, deduplication, and refresh behavior.
- Avoid logging tokens, credentials, personal data, account data, or full sensitive payloads.

## 8. Platform and security

- Review Manifest merge output when components, SDKs, permissions, providers, or intent filters change.
- Minimize exported Activities, Services, Receivers, and Providers; protect intentional exports.
- Validate untrusted Intent extras, deep links, URIs, ClipData, files, and serialized values.
- Prevent intent redirection and implicit-intent interception for sensitive actions.
- Use immutable PendingIntent unless mutation is required and constrained.
- Check runtime permission flows, denial, “don't ask again,” partial grants, and API-level behavior.
- Review FileProvider paths, URI grants, path traversal, and external storage exposure.
- Review WebView JavaScript, native bridges, file/content access, URL allowlists, and TLS handling.
- Prevent cleartext transport or permissive certificate/hostname verification.
- Remove debug/test components, secrets, verbose sensitive logs, and unintended backups from release.
- Check notification/foreground-service permission and behavior changes.
- Check task-affinity/launch-mode changes for spoofing and flow isolation.
- Apply least privilege to SDK initialization and data collection.

## 9. Build, dependencies, and native code

- Verify the compatibility matrix for Gradle, AGP, Kotlin, KSP/KAPT, JDK, Compose compiler/plugin, and NDK.
- Check minSdk/compileSdk/targetSdk changes and every platform behavior change crossed.
- Check dependency version conflicts, removed APIs, transitive Manifest entries, duplicate classes, and method count.
- Inspect R8/ProGuard rules for reflection, JNI, serialization, parcelization, and SDK callbacks.
- Verify product flavors, build types, signing assumptions, BuildConfig values, and resource overlays.
- Check resource shrinking and renamed resources referenced dynamically.
- Inspect APK/AAB ABI contents when `.so` files or native SDKs change.
- Verify 16 KB page-size compatibility for native code and prebuilt native libraries when applicable.
- Check packaging, extractNativeLibs, splits, and supported ABIs.

## 10. Performance and resources

- Flag disk, database, network, binder, crypto, bitmap decode, or heavy computation on the main thread.
- Check N+1 queries/network calls and work repeated per list item, frame, recomposition, or observer emission.
- Bound collections, caches, logs, retries, queues, and bitmap dimensions.
- Close streams, cursors, files, typed arrays, and native resources.
- Check leaks from listeners, callbacks, coroutines, static fields, singleton references, and WebViews.
- Check startup initialization and ContentProvider/SDK auto-init changes.
- Check WorkManager uniqueness, constraints, retry/backoff, input size, and duplicate scheduling.
- Check alarms, wake locks, location, foreground services, and background network/battery cost.
- Demand a concrete hot path or scale before reporting micro-optimizations.

## 11. UI, accessibility, and localization

- Avoid hard-coded user-visible strings and locale-unsafe formatting.
- Verify content descriptions/semantics, roles, state announcements, focus order, and keyboard access.
- Check touch-target size and whether color alone conveys state.
- Exercise font scale, long translations, RTL, dark theme, orientation, and different window sizes when relevant.
- Check text clipping, ellipsis, input constraints, IME actions, focus, and keyboard insets.
- Verify edge-to-edge, system bars, cutouts, gesture navigation, and predictive back behavior when affected.
- Preserve loading/error affordances and prevent double actions.

## 12. Regression test matrix

Choose tests based on the change:

| Change surface | Minimum focused verification |
|---|---|
| Pure business rule | Unit tests for before/after, boundaries, null/empty, and invalid input |
| ViewModel/reducer | State sequence, duplicate event, error, cancellation, recreation |
| Flow/coroutine | Ordering, cancellation, exception, dispatcher, repeated collection, race |
| Room/schema | Fresh install plus every supported migration path with retained data |
| Network mutation | Timeout/retry, duplicate submission, idempotency, malformed/error response |
| Navigation/Intent | Valid/invalid extras, deep link, back stack, result callback, recreation |
| Compose/View UI | State rendering, interaction, lifecycle, accessibility, font scale/RTL if relevant |
| Manifest/permission | Merge result, grant/deny/revoke, external caller, supported API levels |
| Dependency/build | All affected variants, release minification, packaging, runtime smoke test |
| Native SDK | ABI inspection, 16 KB environment when applicable, device/API coverage |
