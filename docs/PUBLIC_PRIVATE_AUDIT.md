# State Zero Public/Private Audit

This audit classifies every tracked Python file in the repository so we can keep the public tree intentional, keep private data out of git, and separate operator tooling from production runtime code.

## Public/Private Rules

- Public runtime code lives under `src/scripts/` and should remain safe to ship.
- Operator-only commands live under `ops/` and can touch tokens, health checks, or repo hygiene tasks.
- Private data generation tooling stays public, but it must write into `STATE_ZERO_PRIVATE_ROOT` or an explicitly chosen non-repo output path.
- Regression tests stay public. They are part of the product safety net, not throwaway scratch work.
- Dev smoke commands should never live behind production CLI flags when they are not part of the supported runtime contract.

## Core Runtime

- `src/scripts/composite.py`
- `src/scripts/creature_utils.py`
- `src/scripts/daily_run_state.py`
- `src/scripts/database_manager.py`
- `src/scripts/emergency_fallback_manager.py`
- `src/scripts/environment_utils.py`
- `src/scripts/google_image_client.py`
- `src/scripts/google_key_router.py`
- `src/scripts/google_video_client.py`
- `src/scripts/image_gen.py`
- `src/scripts/instagram_poster.py`
- `src/scripts/instagram_token_manager.py`
- `src/scripts/lookups.py`
- `src/scripts/notifier.py`
- `src/scripts/openrouter_client.py`
- `src/scripts/pipeline.py`
- `src/scripts/prompts.py`
- `src/scripts/title_utils.py`
- `src/scripts/utils.py`
- `src/scripts/validate.py`
- `src/scripts/whoop_client.py`
- `src/scripts/whoop_token_manager.py`

## Operator-Only Tooling

- `ops/auth_whoop.py`
- `ops/check_repo_hygiene.py`
- `ops/instagram_token_healthcheck.py`

Compatibility shims:
- `src/scripts/auth_whoop.py`
- `src/scripts/instagram_token_healthcheck.py`

These thin wrappers exist only to avoid breaking existing external cron or manual habits while the canonical operator entrypoints live under `ops/`.

## Private-Data Generation Tooling

- `astrology_generator/generate_astrology_yaml.py`
- `astrology_generator/astrology/__init__.py`
- `astrology_generator/astrology/generator.py`
- `astrology_generator/astrology/provider.py`
- `astrology_generator/astrology/vimshottari.py`

Operator note:
- The astrology generator is public code, but its default target is the detected private `astrology/` directory under `STATE_ZERO_PRIVATE_ROOT`.
- It should not write generated `natal.yaml` or `dasha_periods.yaml` into the repository tree.

## Regression Tests

- `tests/test_creature_selection.py`
- `tests/test_depth_ladder.py`
- `tests/test_emergency_fallback_hardening.py`
- `tests/test_environment_selection.py`
- `tests/test_metadata_selection.py`
- `tests/test_recovery_weighting.py`
- `tests/test_reliability_hardening.py`

## Dev-Only Smoke Helpers

- `ops/composite_smoke_test.py`

## Audit Commands

- `python3 ops/check_repo_hygiene.py`
- `python3 -m unittest discover -s tests -v`
- `python3 ops/composite_smoke_test.py --type image`

## Current Decisions

- Keep all tracked tests. They protect real runtime behavior and are not garbage.
- Keep operator tooling public, but out of `src/scripts/` so the runtime lane stays focused.
- Keep the astrology generator public, but document it as private-data tooling rather than public runtime.
- Prefer selective cleanup over mass deletion. At the time of this audit, no tracked Python file is clearly safe to delete outright.
