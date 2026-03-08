# State Zero Verification Audit

Date: 2026-03-09

## Purpose

This audit re-checks the current State Zero pipeline from first principles after the fallback, rescue-run, and reliability hardening work.

The goal is not to add features. The goal is to answer:

1. What is the system supposed to do now?
2. Does the current repository actually do that?
3. What still looks risky, confusing, or operationally dependent?
4. Should the design be simplified before more changes are made?

## How This Started

The original requirement was narrow:

- if the normal daily generation fails, do not miss the day
- post a prebuilt `ERROR 404` fallback card instead

That expanded into five layers:

1. Normal daily pipeline
2. Telegram/manual-first mode with deadline-based auto-generation fallback
3. Emergency post fallback using private prebuilt assets
4. A dedicated `2:00 PM IST` rescue run for WHOOP-never-ready days
5. Crash/rerun/idempotence recovery so partial success does not become duplicate posting

The codebase now reflects all five layers.

## Canonical Behavior Matrix

This is the intended behavior reconstructed from the current repository.

| Scenario | Expected action | Current repo behavior | Status |
| --- | --- | --- | --- |
| Missing required env var | Fail fast, no emergency fallback | `validate.py` exits non-zero before runtime body | Matches |
| Invalid `PIPELINE_DATE` | Fail fast, no emergency fallback | `validate.py` rejects non-ISO dates before `lookups.py` runs | Matches |
| Invalid Instagram token preflight | Fail fast, no emergency fallback | `step_1b_validate_instagram_token()` calls terminal `log_error()` | Matches |
| WHOOP not ready on morning retry run | Mark retryable, release claim, exit cleanly | `lookups.py` exit `2` -> retryable release path | Matches |
| Transient WHOOP/API/network failure before `2:00 PM` | Mark retryable, release claim, exit cleanly | `lookups.py` exit `3` -> retryable release path | Matches |
| WHOOP not ready on `2:00 PM` rescue run | Emergency fallback may post | retryable lookup path escalates to fallback-eligible `PipelineStageError` when `PIPELINE_TERMINAL_RESCUE_RUN=true` | Matches |
| Transient WHOOP/API/network failure on `2:00 PM` rescue run | Emergency fallback may post | retryable external failure escalates on rescue run | Matches |
| Terminal lookup/build failure | Emergency fallback may post | `lookups.py` exit `4` -> fallback-eligible error | Matches |
| Unknown `lookups.py` exit code | Fatal wiring/config issue, not fallback | non-`2`/`3`/`4` exit -> `PipelineStageError(..., fallback_eligible=False)` | Matches |
| Prompt output missing/invalid JSON | Emergency fallback may post | `_load_required_json()` raises fallback-eligible error | Matches |
| Missing `interpretation.txt` only | Should not block posting | `run()` no longer reads `interpretation.txt` for caption/posting | Matches |
| Manual Telegram assets received before deadline | Use manual media | `step_7_9_manual_or_fallback()` prefers manual media | Matches |
| No Telegram assets before deadline | Run automatic generation | `step_7_9_manual_or_fallback()` switches to API generation after deadline | Matches |
| Image generation failure | Emergency fallback may post | `step_7_generate_image()` is fallback-eligible | Matches |
| Video generation failure | Emergency fallback may post | `step_9_generate_video()` is fallback-eligible | Matches |
| Static/animated composite failure | Emergency fallback may post | render steps are fallback-eligible | Matches |
| VPS upload failure | Emergency fallback may post | `step_12_upload_vps()` raises fallback-eligible error | Matches |
| Generated-media Instagram processing/publish failure | Emergency fallback may post once | `step_14_post_instagram()` raises fallback-eligible error only outside emergency fallback | Matches |
| Instagram token failure inside emergency fallback | Fail terminally, do not recurse | emergency fallback token path raises non-fallback-eligible `PipelineStageError` | Matches |
| Successful post followed by archive/DB failure | Leave day as `POSTED`, warning only | `step_15_archive()` is warning-only on failure | Matches |
| Rerun after emergency fallback post already exists | Do not repost; still write fallback observability artifacts | `_run_emergency_fallback()` still writes `emergency_fallback_used.json` and upserts `fallback_posts` on `already_posted=True` | Matches |
| Rerun after normal post exists but archive/card DB is missing | Do not repost; recover archive/DB from existing run artifacts | `skip_posted` claim path triggers archive recovery from the run output dir | Matches |

## Repository Audit Summary

### 1. Preflight Boundary

The repository now has a clean boundary:

- preflight/config/auth failures are operator-managed
- runtime generation/posting failures are fallback-managed

This is consistent in:

- `src/scripts/validate.py`
- `src/scripts/pipeline.py`

That means:

- removing a Google API key is not a valid emergency-fallback drill
- invalid IG auth is not a valid emergency-fallback drill
- an invalid generation model is a valid emergency-fallback drill

### 2. WHOOP Retry vs Rescue Logic

The repository no longer posts the `ERROR 404` card too early for retryable WHOOP-side failures.

Current model:

- before `2:00 PM IST`: retry/release on WHOOP-not-ready or transient WHOOP/API/network failure
- at `2:00 PM IST` rescue run only: escalate those same retryable failures into emergency fallback

This is the correct behavior for the stated product goal:

- keep trying during the day
- do not lose the day if data never arrives

### 3. Emergency Fallback Trigger Scope

The fallback is now narrowly scoped to meaningful runtime failures:

- prompt outputs
- image/video generation
- composite/render
- VPS/public URL prep
- Instagram processing/publish path
- rescue-run WHOOP exhaustion

It is no longer triggered by:

- malformed `PIPELINE_DATE`
- stale docs wiring like `interpretation.txt`
- general preflight/config problems
- nested failures inside the emergency fallback itself

### 4. State and Ownership

Daily state handling is substantially safer than earlier iterations:

- claim ownership is enforced via `run_token`
- corrupt claim files without a matching token are not deleted by another process
- rescue-run WHOOP escalation stops the heartbeat thread before emergency fallback starts
- a confirmed Instagram publish can force `POSTED` state recovery even if ownership moved afterward

Most important invariant now holds:

- a successful post should not later become `FAILED_FATAL`

### 5. Archive and Idempotence

Both normal cards and fallback posts are now idempotent at the DB layer:

- `cards.date` uses upsert semantics
- `fallback_posts.run_date` uses upsert semantics

There are now two separate recovery paths:

- normal posted-day recovery repairs `last_archived_payload.json` + `cards` from existing run output
- emergency-fallback reruns still write `emergency_fallback_used.json` + `fallback_posts`

That is the correct convergence model for crash recovery.

## Proven Bugs From This Audit

No new critical or high-severity repository bugs were proven during this audit pass.

The issues identified in earlier passes were addressed before this report:

- unexpected `lookups.py` exit codes no longer trigger fallback
- `interpretation.txt` is no longer a hidden posting dependency
- fallback reruns no longer skip observability writes
- post-success ownership loss no longer kills the run
- daily state writes now use `flush + fsync + replace`
- normal posted-day reruns can now repair archive/DB artifacts

## Looks Dangerous But Is Correct

These areas look suspicious at first glance but are currently intentional:

### `already_posted` early return inside the main run body

`run()` still returns early if `step_14_post_instagram()` says the day is already `POSTED`.

This is acceptable because:

- the safe crash-recovery path runs earlier at claim time via `skip_posted`
- if a run reaches `step_14_post_instagram()` and discovers `POSTED`, another run has already won the day
- archiving the current run's artifacts in that situation would be wrong

### Emergency fallback token failure does not recurse

Inside emergency fallback, Instagram token failure does not mark itself fallback-eligible.

That is correct because:

- a fallback cannot recursively fallback into itself
- token/auth failures remain terminal/operator-managed at that point

### Post-success cleanup failures only warn

Archive/database failures after publish are warning-only.

That is correct because:

- the post already happened
- preserving `POSTED` is more important than strict cleanup success

## Automated Coverage Status

Current focused coverage in repo:

- `tests/test_emergency_fallback_hardening.py`
- `tests/test_reliability_hardening.py`

Current verified areas include:

- manifest/path safety
- fallback staging behavior
- fallback rerun logging and DB persistence
- retryable WHOOP failure handling
- rescue-run heartbeat stop behavior
- caption-build fallback eligibility
- dry-run behavior without `interpretation.txt`
- nested fallback prevention
- unknown lookup exit codes staying fatal
- post-publish ownership recovery
- archive failure warning-only behavior
- token refresh concurrency
- token cooldown recovery after corrupt state
- configurable refresh threshold behavior
- daily state atomic write `fsync()`
- normal/fallback DB upserts

Automated verification result at audit time:

- `python3 -m py_compile ...` passed
- `python3 -m unittest tests.test_emergency_fallback_hardening tests.test_reliability_hardening` passed
- total passing tests in these suites: `31`

## What Is Still Operationally Unverified

This audit is repository-backed, not a live production drill.

Still operationally dependent:

- Dokploy schedules actually match the documented schedule
- `6:30 AM IST` token healthcheck task exists
- `2:00 PM IST` rescue task exists with:
  - `PIPELINE_TERMINAL_RESCUE_RUN=true`
  - `PIPELINE_MODE=automatic`
- production private runtime still contains the fallback manifest/assets
- public VPS fallback URLs stay live
- real WHOOP rescue behavior on a genuine no-data day
- real Instagram fallback publish path under live conditions

These are not code contradictions. They are deployment/runtime checks.

## Simplification Recommendation

Do not add more fallback layers right now.

Current recommendation:

1. Freeze the design
2. Keep the current five-layer model
3. Run only controlled drills and operational checks
4. Avoid broadening automatic fallback into preflight/config/auth failures

Why:

- the current design is now coherent enough
- most of the earlier confusion came from repeated scope expansion, not from one broken core
- adding more “just in case” branches will increase ambiguity faster than it improves reliability

If simplification is desired later, the best candidate is not the rescue run. The best candidate is consolidating recovery/observability code into shared helpers so normal-post and emergency-fallback convergence use the same recovery primitives.

## Final Verdict

The repository is in a materially better state than when the fallback work started.

At repo level, the system is now coherent:

- normal runs stay normal
- retryable WHOOP failures do not trigger fallback too early
- terminal WHOOP rescue behavior is separate and explicit
- runtime generation/post failures can route into emergency fallback
- reruns converge instead of duplicating posts
- post-success cleanup no longer corrupts the day

This is not "perfect" in the sense of being fully proven in production by every edge case.

It is "stable enough to stop changing behavior and move to controlled operational verification."

## Recommended Next Step

Do not patch more logic immediately.

Instead do:

1. one normal-day observation
2. one manual Telegram day
3. one automatic-after-deadline day
4. one controlled emergency-fallback drill using an invalid image model
5. one later rescue-run drill only if you still need WHOOP-terminal confidence

Until those are run, the remaining uncertainty is operational, not architectural.
