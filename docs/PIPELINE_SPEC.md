# State Zero — Daily Pipeline V2.0

This document is the architecture and operating spec for the State Zero pipeline.
## Overview

This pipeline runs a multi-stage flow once per day that transforms WHOOP biometric data and Vedic dasha timing into a retro sci-fi art card posted to Instagram as a Reel.

Runtime requirement: Python 3.10+ (3.11+ recommended). Python 3.9 is unsupported.

The pipeline has two execution modes. Choose based on cost preference and how much control you want over generation.

---

## Pipeline Modes

### MODE 1 — Full Auto
```
WHOOP data → dasha lookup → 3 AI prompts → JSON prompt → image generation
→ video generation (from image + video prompt) → composite.py → upload VPS → Instagram post
```
No human intervention. Runs entirely on cron. Highest API cost.

### MODE 2 — Telegram Manual-First (Image + Video Manual, Auto Fallback)
```
WHOOP data → dasha lookup → 3 AI prompts → image_prompt.json + video_prompt.txt
→ send both prompts to Telegram → you generate image + video manually and send both back
→ composite.py → upload VPS → Instagram post
→ if manual files not received by deadline: auto-generate image/video via API and continue
```
This is cost-saving mode. Most days you can generate manually. If you do not send both files before the configured deadline, the system automatically falls back to full auto generation and still completes posting.

**Set mode via environment variable:**
```
PIPELINE_MODE=automatic   # Mode 1
PIPELINE_MODE=telegram    # Mode 2
```

---

## File Locations

```
<repo-root>/
├── src/
│   ├── assets/
│   ├── prompts/
│   │   ├── interpretation.md
│   │   ├── creature.md
│   │   ├── environment.md
│   │   ├── json_builder.md
│   │   ├── video.md
│   │   └── metadata_builder.md
│   └── scripts/
│       ├── lookups.py              ← WHOOP + dasha lookup
│       ├── validate.py             ← pre-flight checks
│       ├── prompts.py              ← AI prompt orchestration
│       └── composite.py            ← card rendering engine
├── docs/
│   ├── PIPELINE_SPEC.md
│   └── STATE_ZERO_RULEBOOK.md
└── astrology_generator/

<private-root>/
├── astrology/
│   ├── natal.yaml
│   └── dasha_periods.yaml
└── runtime/
    ├── output/                     ← Per-run generated artifacts and archive payloads
    ├── database/
    │   └── cards.db                ← SQLite post history
    ├── state/
    │   ├── instagram_token_state.json
    │   ├── instagram_token_health_state.json
    │   └── whoop_token_state.json
    └── local_vps/                  ← Local-only ngrok staging folder for laptop tests
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `WHOOP_CLIENT_ID` | WHOOP OAuth client ID |
| `WHOOP_CLIENT_SECRET` | WHOOP OAuth client secret |
| `OPENROUTER_API_KEY` | Primary LLM API key (used for dasha prompts + JSON builder + video prompt) |
| `GOOGLE_API_KEY_PRIMARY` | Google API key for image (Imagen 3) and video (VEO 3) generation |
| `GOOGLE_API_KEY_FALLBACK` | Fallback Google API key to circumnavigate rate limits |
| `GOOGLE_IMAGE_MODEL` | Specific image model to use (e.g. gemini-3.1-flash-image-preview) |
| `GOOGLE_VIDEO_MODEL` | Specific video model to use (e.g. veo-3.1-fast-generate-preview) |
| `GOOGLE_VIDEO_TIMEOUT_SECONDS` | Max timeout for video generation |
| `GOOGLE_VIDEO_POLL_SECONDS` | Polling interval while waiting for video rendering |
| `INSTAGRAM_ACCESS_TOKEN` | Long-lived Instagram Graph API token (60-day expiry) |
| `INSTAGRAM_USER_ID` | Instagram Business or Creator account numeric user ID |
| `INSTAGRAM_GRAPH_API_VERSION` | Graph API version for Instagram publishing (default: `v25.0`) |
| `INSTAGRAM_PROCESSING_MAX_ATTEMPTS` | Max media-container processing attempts after terminal processing errors (default: `2`) |
| `INSTAGRAM_PROCESSING_RETRY_DELAY_SECONDS` | Delay before retrying a failed media-container processing attempt (default: `30`) |
| `INSTAGRAM_AUTO_REFRESH_MODE` | `off` (validate-only), `legacy_ig` (legacy endpoint), or `hybrid` (fail-fast + proactive refresh with cooldown) |
| `INSTAGRAM_REFRESH_THRESHOLD_DAYS` | In `hybrid`, refresh when days-to-expiry <= threshold (default: 14) |
| `INSTAGRAM_REFRESH_COOLDOWN_HOURS` | Minimum hours between auto-refresh attempts (default: 12) |
| `FACEBOOK_APP_ID` | Optional Meta app id for token expiry introspection (`debug_token`) |
| `FACEBOOK_APP_SECRET` | Optional Meta app secret for token expiry introspection (`debug_token`) |
| `INSTAGRAM_TOKEN_HEALTHCHECK_ENABLED` | Enable scheduled token health checker alerts (default: true) |
| `INSTAGRAM_TOKEN_ALERT_DAYS` | Comma-separated alert thresholds in days (default: `14,7,3,1`) |
| `VPS_PUBLIC_BASE_URL` | Public base URL for hosted files, e.g. `https://your-ip/media` |
| `EMERGENCY_FALLBACK_ENABLED` | `true` enables the private `error_404_v1` emergency post fallback after eligible pipeline failures |
| `PIPELINE_TERMINAL_RESCUE_RUN` | `true` forces terminal rescue behavior on the dedicated 2:00 PM IST run; the pipeline also infers rescue mode automatically once the configured local deadline has passed |
| `VPS_SSH_HOST` | Host IP or domain for SCP/SSH uploading final assets to VPS |
| `VPS_SSH_USER` | Target user for the VPS connection |
| `VPS_SSH_PATH` | Server directory path for uploaded assets |
| `PIPELINE_MEDIA_MODE` | `local_test` for laptop + ngrok, `live_vps` for real VPS upload |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for sending and receiving media |
| `TELEGRAM_CHAT_ID` | Your personal Telegram chat ID the bot communicates with |
| `PIPELINE_MODE` | `automatic` / `telegram` |
| `PIPELINE_TIMEZONE` | Local timezone for manual deadline, e.g. `Asia/Kolkata` |
| `PIPELINE_MANUAL_DEADLINE_LOCAL` | Absolute fallback time in local timezone, e.g. `14:00` |
| `PIPELINE_MANUAL_DEADLINE_MODE` | `run_date` (production default) or `from_now` (for backfilled manual testing) |
| `PIPELINE_MANUAL_WINDOW_MINUTES` | Manual window length for `from_now` mode, e.g. `120` |
| `PIPELINE_TELEGRAM_POLL_SECONDS` | Poll interval while waiting for manual uploads |
| `PIPELINE_MANUAL_MATCH_STRICT` | `true` = only reply/token matches; `false` = also accept fresh uploads after instruction (default) |
| `TELEGRAM_NOTIFY_ERRORS` | Enable/disable error notifications (default: true) |
| `TELEGRAM_NOTIFY_SUCCESS` | Enable/disable success notifications (default: true) |
| `TELEGRAM_ERROR_DEDUPE_SECONDS` | Dedupe window for repeated errors (default: 300) |
| `TELEGRAM_NOTIFY_INCLUDE_STDERR_LINES` | Lines of stderr/stdout to include in error alerts (default: 40) |

---

## Execution Matrix (What Actually Runs)

### Core runtime switches

| Switch | Values | Behavior |
|---|---|---|
| `PIPELINE_MODE` | `automatic` / `telegram` | `automatic` = always generate image/video via APIs. `telegram` = send prompts, wait for manual uploads, fallback to automatic if deadline missed. |
| `PIPELINE_POST_TO_INSTAGRAM` | `true` / `false` | `true` = post to Instagram (live mode). `false` = dry-run (skip Instagram publish path, skip VPS upload). |
| `PIPELINE_MANUAL_DEADLINE_MODE` | `run_date` / `from_now` | `run_date` uses `PIPELINE_DATE + PIPELINE_MANUAL_DEADLINE_LOCAL`; `from_now` uses `PIPELINE_MANUAL_WINDOW_MINUTES`. |
| `PIPELINE_MANUAL_MATCH_STRICT` | `true` / `false` | `true` only accepts reply-to prompt messages or run token match; `false` also accepts fresh media after instruction time. |

### Effective pipeline scenarios

| PIPELINE_MODE | PIPELINE_POST_TO_INSTAGRAM | Manual Wait? | Auto Fallback? | VPS Upload | Instagram Post? |
|---|---|---|---|---|---|
| `automatic` | `true` | No | N/A | Yes | Yes |
| `automatic` | `false` | No | N/A | No | No |
| `telegram` | `true` | Yes | Yes | Yes | Yes |
| `telegram` | `false` | Yes | Yes | No | No |

Notes:
- In `telegram` mode, manual uploads must include both image and video before deadline, otherwise auto-fallback generation runs.
- Telegram uploads accepted: image/video documents, Telegram `video`, and Telegram `photo` (highest resolution chosen).
- Replying to instruction or prompt chunks is supported; token caption/text matching is also supported.
- When `PIPELINE_POST_TO_INSTAGRAM=false` (dry-run), pipeline still produces local artifacts (`card_final.png`, `card_final.mp4`) but skips VPS upload and Instagram publish.
- Telegram dry-run (`telegram + false`) still performs full manual wait/fallback behavior — dry-run only affects the Instagram publish path.

### Instagram token behavior

| `INSTAGRAM_AUTO_REFRESH_MODE` | Behavior |
|---|---|
| `off` | Validate only; fail fast if invalid/expired. |
| `legacy_ig` | Validate; attempt legacy `graph.instagram.com` refresh on failure/age rules. |
| `hybrid` | Validate + proactive refresh near expiry (`INSTAGRAM_REFRESH_THRESHOLD_DAYS`) with cooldown (`INSTAGRAM_REFRESH_COOLDOWN_HOURS`) + fail-fast guard. |

Important:
- Pipeline runs token preflight before expensive steps (WHOOP/LLM/image/video), unless `PIPELINE_POST_TO_INSTAGRAM=false` (dry-run).
- Expiry countdown alerts require `FACEBOOK_APP_ID` + `FACEBOOK_APP_SECRET` for `debug_token`.

### Validation gotchas (common confusion)

- `validate.py` requires VPS vars when `PIPELINE_POST_TO_INSTAGRAM=true`:
  - `VPS_PUBLIC_BASE_URL`, `VPS_SSH_HOST`, `VPS_SSH_USER`, `VPS_SSH_PATH`.
- When `EMERGENCY_FALLBACK_ENABLED=true`, `validate.py` always checks the private fallback manifest and local fallback media integrity.
- Before the cutoff, validation enforces full pipeline readiness. At/after the cutoff, validation switches to fallback-only rescue readiness and no longer requires WHOOP, OpenRouter, Google, or Telegram inputs if the fallback can still be posted.
- `PIPELINE_TERMINAL_RESCUE_RUN=true` is still supported for the dedicated 2:00 PM IST rescue schedule, but the pipeline also self-promotes into rescue mode once the configured local deadline has passed.
- ngrok is for `local_test` only. Production emergency fallback manifests must use stable VPS-hosted public URLs, not ngrok URLs.
- WHOOP mock data (`PIPELINE_MOCK_DATA`) is no longer supported — real WHOOP API data is always required.
- Python 3.10+ is required; Python 3.9 is unsupported.
- LLM keys are now canonical:
  - `OPENROUTER_API_KEY`
  - `GOOGLE_API_KEY_PRIMARY`
  - `GOOGLE_API_KEY_FALLBACK`
  (`LLM_API_KEY` is no longer part of active runtime config.)

### Recommended presets

1) **Production manual-first (live)**
```bash
PIPELINE_MODE=telegram
PIPELINE_POST_TO_INSTAGRAM=true
PIPELINE_MANUAL_DEADLINE_MODE=run_date
PIPELINE_MANUAL_MATCH_STRICT=false
INSTAGRAM_AUTO_REFRESH_MODE=hybrid
EMERGENCY_FALLBACK_ENABLED=false
PIPELINE_TERMINAL_RESCUE_RUN=false
```

2) **Production manual-first (dry-run)**
```bash
PIPELINE_MODE=telegram
PIPELINE_POST_TO_INSTAGRAM=false
PIPELINE_MANUAL_DEADLINE_MODE=run_date
PIPELINE_MANUAL_MATCH_STRICT=false
```

3) **Production fully automatic (live)**
```bash
PIPELINE_MODE=automatic
PIPELINE_POST_TO_INSTAGRAM=true
INSTAGRAM_AUTO_REFRESH_MODE=hybrid
```

4) **Production fully automatic (dry-run)**
```bash
PIPELINE_MODE=automatic
PIPELINE_POST_TO_INSTAGRAM=false
```

---

## Telegram Notifications

The pipeline includes a centralized notification system that sends alerts via Telegram for errors, warnings, status updates, and successful posts.

### Notification Types

| Type | When Triggered | Telegram Message |
|------|----------------|------------------|
| **Error** | Pipeline step fails, subprocess error, uncaught exception | 🔴 with step name, error type, message, stderr/stdout tail |
| **Warning** | Manual upload validation fails (small image, invalid video) | 🟡 with actionable guidance to resend |
| **Status** | Automatic fallback triggered, deadline reached | 🔵 with status reason |
| **Success** | Real Instagram post completed | 🎉 card_final.mp4 document + permalink |
| **Dry Run** | Dry-run mode completed | ℹ️ dry run completed (no post made) |

### Success Notification Contract

On successful real Instagram publish, the pipeline sends:
1. `card_final.mp4` as a Telegram document
2. Instagram public permalink in message text

**No PNG or other artifacts are sent** - only the MP4 and permalink.

### Error Deduplication

To prevent notification spam during repeated failures:
- First error: sent immediately
- Repeated same error: throttled (1 alert per 5 minutes by default)
- Configure via `TELEGRAM_ERROR_DEDUPE_SECONDS`

### Manual Ingestion Warnings

In Telegram manual mode, validation errors send **warnings** (not fatal errors) so you can resend corrected files:
- "Image too small (WxH). Minimum is 900x1200. Please resend."
- "Video invalid/unreadable. Please resend MP4."
- "Video duration too short. Minimum is 1 second."

These allow the pipeline to continue waiting for valid files until the deadline.

---

## Scheduled Execution

```bash
30 6 * * * cd /path/to/state-zero && python3 ops/instagram_token_healthcheck.py >> /var/log/state-zero-healthcheck.log 2>&1
0,30 7-12 * * * cd /path/to/state-zero && python3 src/scripts/pipeline.py >> /var/log/state-zero.log 2>&1
0 13 * * * cd /path/to/state-zero && python3 src/scripts/pipeline.py >> /var/log/state-zero.log 2>&1
0 14 * * * cd /path/to/state-zero && PIPELINE_TERMINAL_RESCUE_RUN=true PIPELINE_MODE=automatic python3 src/scripts/pipeline.py >> /var/log/state-zero-rescue.log 2>&1
```

Notes:
- `6:30 AM IST` healthcheck runs before the first `7:00 AM IST` pipeline attempt.
- The `2:00 PM IST` rescue run is the final decision point for any unresolved pre-post failure, not just WHOOP-missing days.
- Keep the normal live pipeline in `PIPELINE_MODE=telegram`; the rescue schedule overrides to `PIPELINE_MODE=automatic` so it does not depend on the manual deadline already being expired.
- Before `2:00 PM IST`, any unresolved pre-post failure is released as retryable instead of posting the emergency fallback early.
- On the `2:00 PM IST` rescue run, those same unresolved pre-post failures escalate into the emergency fallback if they still have not recovered.

## Manual Fallback Runbook

If the fallback package itself is broken or the fallback publish attempt fails, the emergency fallback cannot be auto-posted. In that case:

1. Save the failure evidence:
   - daily state JSON
   - Dokploy run log / error message
   - any partial Instagram permalink if one exists
2. If the issue cannot be fixed quickly, manually post the already-hosted fallback assets:
   - `https://state-zero-media.notanother.in/fallback/error_404_v1/card.mp4`
   - `https://state-zero-media.notanother.in/fallback/error_404_v1/card.png`
3. Use the fixed fallback caption:

```text
ERROR 404

State Zero hit a pipeline fault today, so the emergency fallback card posted instead. Regular generation resumes next run.

#statezero #dailyart #generativeart
```

**Failure classification:**
- **Retryable before cutoff** — Any pre-post failure before `2:00 PM IST`, including WHOOP lookup issues, prompt/generation/render failures, VPS upload failures, and Instagram main-post failures. The pipeline releases these for cron retry.
- **Terminal rescue / auto-fallback** — Any of those same pre-post failures that still exist once the final rescue trigger runs. These route into the emergency fallback automatically.
- **Fatal no-post** — The fallback package is unavailable/invalid, or the emergency fallback publish attempt itself fails.

Daily state files now include `failure_classification` values such as `validation`, `lookup_not_ready`, `generation`, `upload`, `instagram_main_post`, `fallback_unavailable`, and `fallback_publish_failed`.

Important:
- `interpretation.txt` is still generated by the prompt stack, but it is not a posting-critical dependency. A missing `interpretation.txt` should not trigger the emergency fallback on an otherwise healthy run.

---

## THE FULL PIPELINE

### Data Dependency Hierarchy

This is the exact order in which data feeds into data. The coding agent must not invert any dependency:

```
WHOOP: Strain → energy zone (needed before environment prompt)
          ↓
DASHA: date → dasha_periods.yaml → 5 planets × natal.yaml → sign/house/dignity
          ↓
    ├─→ AI Prompt 1 → interpretation.txt (theme)
    ├─→ AI Prompt 2 → creature.txt (independent — no energy zone, no WHOOP)
    └─→ AI Prompt 3 → environment.txt (constrained by energy zone only)
          ↓
WHOOP: Recovery × Sleep Score → behavioral matrix → body keywords, art keywords, one-liner
WHOOP: Sleep Score → depth level + depth keywords (dual role - SPATIAL ONLY)
WHOOP: Sleep Hours → moon count
          ↓
Concept string assembled from all above
          ↓
JSON built (via AI Prompt 4):
    - Input: concept + art keywords + environment + creature + interpretation
    - LLM selects blend option A/B/C based on full scene (art keywords heavily influence)
    - LLM fills complete JSON with selected blend option applied
          ↓
Card metadata extracted (via AI Prompt 5)
          ↓
Image generated → video prompt built (AI Prompt 6 or templated)
          ↓
Video generated → composite → upload → post → archive → database
```

**Key dependency rules:**
- Energy zone must exist before Prompt 3 (environment) runs
- Interpretation (Prompt 1) output feeds into Prompt 2 (creature) for thematic context only
- Behavior matrix, depth, and moons are WHOOP-only — never influenced by dasha
- **Blend option is selected by LLM in JSON building step** — art keywords heavily influence, but full scene considered
- Blend option is NOT pre-calculated, NOT mechanically derived from environment type
- Creature and environment are always selected independently — no correlation enforced
- **Depth keywords are SPATIAL only (where you are), behavioral matrix art keywords are ATMOSPHERIC (how it feels)**
- **Data timing:** Strain from YESTERDAY, Recovery/Sleep from TODAY, Dasha for TODAY, Date displays TODAY

---

## PROMPT TEMPLATE SPECIFICATIONS

The pipeline uses 6 AI prompts. Full templates are in `src/prompts/`. Here are their input/output specs:

### `src/prompts/interpretation.md`

**Purpose:** Generate 2-sentence archetypal interpretation from dasha planets

**Inputs:** `{ascendant}`, `{moon_nakshatra}`, all 5 dasha planets with `{planet}`, `{sign}`, `{house}`, `{dignity}`

**Output:** Exactly 2 sentences, mystical but specific, grounded in house lordships and dignities

**Example:** "Disciplined partnership pressure meets intuitive career expression — detachment from comfort zones opens space for cautious, tested gains."

---

### `src/prompts/creature.md`

**Purpose:** Select single creature archetype that embodies the period's energy

**Inputs:** `{ascendant}`, `{moon_nakshatra}`, all 5 dasha planets with sign/house/dignity, `{interpretation}` from Prompt 1

**CRITICAL:** Do NOT pass energy zone, environment options, or any WHOOP metrics

**Output:** Single creature name + 1-sentence reasoning

**Example:** "Serpent — shedding skin embodies the detachment theme, patient movement reflects disciplined pressure, silent intelligence mirrors the intuitive career awareness."

**Note:** Creature sources can be real fauna, Hindu/Vedic mythology, world mythology, or invented alien creatures

---

### `src/prompts/environment.md`

**Purpose:** Select environment type from energy-zone-constrained options

**Inputs:** `{energy_zone}` (LOW/MEDIUM/HIGH), `{interpretation}` from Prompt 1, `{environment_options}` (list of valid environments for this energy zone)

**CRITICAL:** Do NOT pass creature or any WHOOP behavioral metrics

**Output:** Single environment name + 1-sentence justification

**Example:** "Crystalline — structured geometric growth matches the disciplined expansion theme while sharp optical clarity fits the interpretive direction."

**Note on `{environment_options}` construction:** This placeholder must be filled by `prompts.py` before calling the LLM. Based on the `energy_zone` value from `daily_data.json`, construct a formatted list from the environment tables in STEP 4, Prompt 3 section below (lines 596-629). For example, if energy_zone is "HIGH", format the 6 HIGH environments as a bulleted list.

---

### `src/prompts/json_builder.md`

**Purpose:** Construct complete image generation JSON following the master template

**Inputs:** All data from `daily_data.json`, `interpretation.txt`, resolved creature/environment selections (`creature_selected.txt` / `environment_selected.txt` when available, otherwise raw outputs), full master JSON template

**Output:** Complete filled JSON ready for image generation

**Note:** This is a comprehensive template with material quality lookup tables, blend option selection logic, forbidden/required language rules, brightness enforcement, and self-check validation. See the full template file for details.

---

### `src/prompts/video.md`

**Purpose:** Construct video animation prompt from image concept

**Inputs:** All data from `daily_data.json`, resolved environment selection, blend option, matrix body keywords, art keywords, one-liner, recovery zone, and energy zone

**Output:** Complete video prompt ready for video generation

**Format:** Structured prompt with speed, camera movement, environment motion, particle effects, and style specifications mapped from art keywords

---

### `src/prompts/metadata_builder.md`

**Purpose:** Extract card title and scene description from image JSON

**Inputs:** `{image_prompt_json}`, `{date_display}`

**Output:** JSON with `title` (1-2 words, max 13 chars, UPPERCASE), `scene_description` (under 90 chars, present tense), `date_display`

**Example:**
```json
{
  "title": "STATIC HOLLOW",
  "scene_description": "Charged geological formations rise through storm mist as two moons drift overhead.",
  "date_display": "01 MAR 2026"
}
```

---

## STEP-BY-STEP PIPELINE

### STEP 1 — Pre-flight Validation

Run `validate.py` before anything else.

```bash
python3 src/scripts/validate.py
```

**Checks:**
- All required environment variables present and non-empty
- `data/natal.yaml` exists and has valid structure
- `data/dasha_periods.yaml` exists and has valid structure
- `output/` directory exists (create if not)
- WHOOP API responds to a test call
- Telegram bot can send a message to `TELEGRAM_CHAT_ID`
- `database/` directory exists (create if not)

**If any check fails: stop immediately, report which check failed, do not proceed.**

**Output:** Prints "✓ All validations passed" or exits with error

---

### STEP 2 — Pull WHOOP Data and Compute Mappings

```bash
python3 src/scripts/lookups.py --output output/daily_data.json
```

#### ⏰ CRITICAL: Data Timing Logic

**When the pipeline runs** (CRON time TBD, likely morning India time):

At the moment you wake up:
- **TODAY's Strain** = 0 (you just woke up, no activity accumulated yet)
- **TODAY's Recovery & Sleep Score** = calculated from YESTERDAY's sleep (this is what you start your day with)

**Therefore, the data pull strategy is:**

```python
import pytz
from datetime import datetime, timedelta

# Get current date in India timezone
india_tz = pytz.timezone('Asia/Kolkata')
now_india = datetime.now(india_tz)
today = now_india.date()              # e.g., 2026-03-02
yesterday = today - timedelta(days=1)  # e.g., 2026-03-01

# Pull from WHOOP API:
strain = whoop.get_strain(date=yesterday)        # YESTERDAY's strain (influenced last night's recovery)
recovery = whoop.get_recovery(date=today)        # TODAY's recovery (from yesterday's sleep)
sleep_score = whoop.get_sleep_score(date=today)  # TODAY's sleep score (from yesterday's sleep)
sleep_hours = whoop.get_sleep_hours(date=today)  # TODAY's sleep duration (from yesterday's sleep)

# Dasha lookup:
dasha = lookup_dasha(date=today)  # TODAY's dasha period

# Date display on card:
date_display = today.strftime("%d %b %Y").upper()  # "02 MAR 2026"
```

**The card represents:** Yesterday's strain + Today's recovery/sleep + Today's dasha

**Example:** CRON runs March 2 morning → Card shows: Strain from March 1 + Recovery/Sleep from March 2 + Dasha for March 2 + Date displays "02 MAR 2026"

---

**Pull from WHOOP API:**
- Strain score (YESTERDAY's cycle - the strain that influenced last night's recovery)
- Recovery % (TODAY's recovery - calculated from yesterday's sleep)
- Sleep score % (TODAY's sleep score - calculated from yesterday's sleep)
- Sleep duration in hours (TODAY's sleep duration - from yesterday's sleep)

#### Strain → Energy Zone

| Strain | Energy Zone | Character |
|--------|-------------|-----------|
| 0–9 | LOW | Passive, static, mineral |
| 9–14 | MEDIUM | Active, flowing, organic |
| 14+ | HIGH | Intense, explosive, extreme |

Strain sets energy level only, not quality. A 16 from a hard workout and a 16 from stress both map to HIGH. Recovery modulates how that energy feels — not strain.

#### Recovery % → Recovery Zone

| Recovery | Zone |
|----------|------|
| 76%+ | HIGH |
| 55–76% | MID |
| 0–55% | LOW |

#### Sleep Score % → Sleep Zone (Dual Role)

Sleep Score does double duty: it sets the environment depth level AND feeds the behavior matrix.
The mapping bands for sleep, recovery, strain, and moon count are working creative heuristics tuned against recent historical distributions/clusters to improve output balance, and may be periodically recalibrated. In this pass, only sleep-score thresholds changed; recovery/strain/moon thresholds remain numerically unchanged.

| Sleep Score | Zone | Depth Level | Depth Keywords (SPATIAL ONLY) | Visibility |
|-------------|------|-------------|-------------------------------|------------|
| 84%+ | SURFACE | **SURFACE** | Celestial, Elevated, Bright, Open | 70–100% visible |
| 78–83% | MID-DEPTH | **MID-DEPTH** | Sheltered, Cavern, Enclosed, Filtered | 50–70% visible |
| 72–77% | DEEP | **DEEP** | Subterranean, Obscured, Limited, Buried | 40–50% visible |
| <72% | ABYSS | **ABYSS** | Void, Compressed, Primordial, Darkness | 40% minimum |

**CRITICAL: Depth keywords describe WHERE you are (spatial position), NOT how it feels (atmosphere). Atmospheric mood comes ONLY from behavioral matrix art keywords.**

#### Sleep Hours → Moon Count

| Sleep Hours | Moon Count | Display |
|-------------|------------|---------|
| 7.5h+ | 3 moons | Full bright moons |
| 6–7.5h | 2 moons | Mix of full and crescents |
| <6h | 1 moon | Crescent or half moon |

Moons always appear in upper third of frame. Size and brightness vary with depth (dimmer in abyss). Color tinted by environment atmosphere. In video they drift slowly across sky.

#### Behavior Matrix — Recovery Zone × Sleep Score Zone (12 states)

This is the core modulation system. It determines HOW the environment behaves — the visual mood, physical texture, and one-liner directing the AI. Same environment (e.g. volcanic) looks radically different across all 12 states.

| Recovery | Sleep Score | Body Keywords | Art Keywords | One-liner |
|----------|-------------|---------------|--------------|-----------|
| HIGH | SURFACE | Sharp, restored, charged | Luminous, expansive, serene | Peak state — wide open landscape, nothing blocking the horizon, everything exactly where it should be |
| HIGH | MID-DEPTH | Solid, warm, capable | Flowing, balanced, harmonious | Well recovered with slight residual weight — moves smoothly, depth visible but unthreatening |
| HIGH | DEEP | Quiet, functional, unhurried | Still, subdued, restrained | Body healed but sleep was thin — capable but dimmer, nothing urgent pressing through |
| HIGH | ABYSS | Stable, disconnected, autopilot | Suspended, stark, vacant | Body fully restored, presence didn't follow — everything intact, nothing inhabited |
| MID | SURFACE | Functional, understated, incomplete | Measured, subdued, indifferent | Slept well, body didn't fully follow — functional and present, but the gap between rest and readiness is quietly there |
| MID | MID-DEPTH | Passive, coasting, carrying weight | Drifting, muted, burdened | Going through the motions with a slight drag — coasting, but the body adds a small tax to every step |
| MID | DEEP | Slow, foggy, resistant | Heavy, dim, pressured | Everything costs slightly more than it should — atmosphere pressing inward, low visibility, small effort for small return |
| MID | ABYSS | Hollow, grinding, close to breaking | Fractured, turbulent, consuming | Both the body and the night failed — hollow at the center, grinding without traction, the surface holds but nothing beneath it does |
| LOW | SURFACE | Tense, wired, fraying | Taut, brittle, unstable | Yesterday's strain held through the night — sleep arrived but the tension didn't release, still wired and stretched past comfortable |
| LOW | MID-DEPTH | Drained, numb, fading | Sinking, stripped, oppressive | Both metrics pulling down. Bare. No colour, no energy. Just form getting through |
| LOW | DEEP | Wrecked, shutdown, leaden | Collapsed, smoldering, suffocating | Day after the damage — post-event silence, everything cooling into wreckage and ash |
| LOW | ABYSS | Destroyed, void, primal | Crushing, devastated, primordial | Complete system failure. Nothing left. The landscape is what remains after everything already collapsed |

#### Output — `output/daily_data.json`

```json
{
  "date": "2026-03-02",
  "date_display": "02 MAR 2026",
  "strain": 14.56,
  "recovery_pct": 51,
  "sleep_score_pct": 84,
  "sleep_hours": 7.08,
  "energy_zone": "HIGH",
  "recovery_zone": "LOW",
  "sleep_score_zone": "SURFACE",
  "moon_count": 2,
  "depth_level": "SURFACE",
  "depth_keywords": ["Celestial", "Elevated", "Bright", "Open"],
  "visibility_range": "70-100%",
  "behavior_matrix": {
    "body_keywords": ["Tense", "wired", "fraying"],
    "art_keywords": ["Volatile", "brittle", "unstable"],
    "one_liner": "Body under strain despite sleep — pressure with nowhere to go, sharp edges, about to fracture"
  }
}
```

**Note:** Blend option is NOT calculated here - it will be determined by LLM in STEP 5 when building the JSON, based on the full scene concept (art keywords + environment + creature + interpretation).

---

### STEP 3 — Dasha Lookup

Run within `lookups.py`. Look up today's dasha period from `data/dasha_periods.yaml` and cross-reference with `data/natal.yaml`.

#### About the Dasha Data

`data/dasha_periods.yaml` contains approximately 3,600 pre-fetched Prana-level entries covering 2026–2031. Raw data was pulled from the Rishi Astrology website (API endpoints exposed in their web app). A custom Python calculator then computed exact Prana period boundaries between the raw entries, producing a complete contiguous lookup table with no gaps.

`data/natal.yaml` is a one-time permanent file containing birth chart planetary data. Never changes.

#### YAML Schemas

**`data/natal.yaml`** — fill once, never changes:

```yaml
natal:
  ascendant: ""          # lagna/rising sign
  moon_nakshatra: ""     # janma nakshatra at birth

  planets:
    Sun:     { sign: "", house: , dignity: "" }
    Moon:    { sign: "", house: , dignity: "" }
    Mars:    { sign: "", house: , dignity: "" }
    Mercury: { sign: "", house: , dignity: "" }
    Jupiter: { sign: "", house: , dignity: "" }
    Venus:   { sign: "", house: , dignity: "" }
    Saturn:  { sign: "", house: , dignity: "" }
    Rahu:    { sign: "", house: , dignity: "" }
    Ketu:    { sign: "", house: , dignity: "" }
```

Dignity values must be exactly one of: `exalted` / `own` / `friendly` / `neutral` / `enemy` / `debilitated`

Why these fields: `ascendant` → AI derives which houses each planet rules. `moon_nakshatra` → determines dasha sequence. `sign` → planet's energy flavour. `house` → which life area activates. `dignity` → how strongly the planet expresses.

**`data/dasha_periods.yaml`** — pre-fetched, covers 2026–2031:

```yaml
periods:
  - start: "2026-02-24"
    end:   "2026-02-27"
    maha:       "Saturn"
    antar:      "Mercury"
    pratyantar: "Ketu"
    sookshma:   "Jupiter"
    prana:      "Moon"

  - start: "2026-02-27"
    end:   "2026-03-01"
    maha:       "Saturn"
    antar:      "Mercury"
    pratyantar: "Ketu"
    sookshma:   "Jupiter"
    prana:      "Mars"
  # ... all periods through 2031
```

Rules: all 5 levels written explicitly in every row — no inheritance. Every Prana period is its own entry. Dates are fully contiguous with no gaps.

#### Lookup Logic

```python
# Input: today's date
# Scan dasha_periods.yaml → find row where start <= today <= end
# Extract: maha, antar, pratyantar, sookshma, prana planet names
# For each planet → cross-reference natal.yaml → get sign, house, dignity
```

**Error handling:** If date not found (outside 2026-2031 range), stop pipeline and report error.

#### Dasha Level Reference

| Level | Duration | Role |
|-------|----------|------|
| Maha | Years | Overarching life theme |
| Antar | Months | Sub-theme within Maha |
| Pratyantar | ~3–4 weeks | Refinement of Antar |
| Sookshma | ~3–10 days | Daily texture |
| Prana | ~1–3 days | True daily seed |

#### Append to `output/daily_data.json`

```json
{
  "dasha": {
    "maha": "Venus",
    "antar": "Venus",
    "pratyantar": "Venus",
    "sookshma": "Rahu",
    "prana": "Rahu",
    "planets_detail": {
      "Venus": { "sign": "Leo", "house": 11, "dignity": "enemy" },
      "Rahu": { "sign": "Cancer", "house": 10, "dignity": "neutral" }
    }
  },
  "natal_context": {
    "ascendant": "Aquarius",
    "moon_nakshatra": "Hasta"
  }
}
```

---

### STEP 4 — Three Independent Dasha AI Prompts

**CRITICAL: Three completely separate LLM API calls. Never combine into one prompt. Never pass environment or WHOOP data to creature prompt. Never pass creature to environment prompt.**

Run via `prompts.py` orchestrator script.

```bash
python3 src/scripts/prompts.py --data output/daily_data.json
```

This script:
1. Reads each prompt template from `src/prompts/`
2. Loads the exact `--data` path when provided, otherwise falls back to the run-date output path
3. Fills placeholders from `daily_data.json` and resolved selected outputs where required
4. Calls LLM API separately for each
5. Saves outputs to `output/`

#### Prompt 1 — Interpretation Theme

Template: `src/prompts/interpretation.md`

Input:
- `natal_context.ascendant`
- `natal_context.moon_nakshatra`
- All 5 dasha planets with sign, house, dignity

Output: 2-sentence archetypal theme grounded in house lordships and planetary dignities. Mystical but specific — not generic word salad.

Save to: `output/interpretation.txt`

Example:
> "Disciplined partnership pressure meets intuitive career expression — detachment from comfort zones opens space for cautious, tested gains."

---

#### Prompt 2 — Creature (Completely Independent)

Template: `src/prompts/creature.md`

Input:
- `natal_context.ascendant` and `moon_nakshatra`
- All 5 dasha planets with sign, house, dignity
- Interpretation text from Prompt 1 (thematic context only)

**Do NOT pass: energy zone, environment options, WHOOP metrics.**

Output: single creature archetype + 1-sentence reasoning. Draw from real fauna, Vedic/Hindu mythology, world mythology, or invented alien creature. No automatic pairings enforced by the prompt.

Save to: `output/creature.txt`

Example:
> "Moth — delicate beauty drawn obsessively toward unreachable light, transforming through cycles, moving in networked swarms, revealing intricate patterns only under specific illumination."

This independence is the point: Moth can appear in volcanic, ice, crystalline, any environment. Phoenix in ocean. Whale in desert. All valid. Forced pairings (Phoenix+Fire, Whale+Ocean) are explicitly unwanted.

---

#### Prompt 3 — Environment (Energy-Constrained)

Template: `src/prompts/environment.md`

Input:
- Energy zone from `daily_data.json`
- Interpretation text from Prompt 1
- Only the environment options valid for the current energy zone (list below)

**Do NOT pass: creature.**

**Valid environments by energy zone:**

**LOW (Strain 0–9) — passive, static, mineral:**

| Environment | Materials |
|-------------|-----------|
| Frozen/Ice | Transparent ice, frost, frozen atmospheric effects |
| Crystal Caves | Angular crystals, gems, prismatic light refraction |
| Stone Monuments | Weathered stone, granite, ancient carved formations |
| Mist/Fog Realms | Volumetric fog, obscured visibility, moisture |
| Void/Space (Low) | Cosmic dust, minimal light, deep space darkness |
| Glacial Valley | Polished bedrock, glacial moraine, still cold tarns, smooth U-shaped rock walls, ancient carved silence |

**MEDIUM (Strain 9–14) — active, flowing, organic:**

| Environment | Materials |
|-------------|-----------|
| Ocean/Underwater | Water, caustics, marine light patterns, aquatic depth |
| Forest/Jungle | Bark, leaves, roots, organic growth, green filtered light |
| Wind/Sky Realms | Clouds, air currents, atmospheric layers, open sky |
| Cave Systems | Limestone, dripping water, stalactites, subterranean chambers |
| Desert (Calm) | Sand, sandstone, dunes, warm earth tones |
| Bioluminescent | Organic tissue, natural glow, living light sources |

**HIGH (Strain 14+) — intense, explosive, extreme:**

| Environment | Materials |
|-------------|-----------|
| Volcanic | Volcanic rock, magma, lava flows, intense heat glow |
| Lightning/Storm | Energy arcs, charged atmosphere, electrical discharge |
| Plasma/Nebula | Glowing plasma, cosmic gas, stellar nursery effects |
| Crystalline (Active) | Growing crystals, sharp formations, intense light refraction |
| Desert (Intense) | Cracked earth, heat distortion, scorched terrain |
| Fire Realms | Fire, smoke, ash, ember glow, combustion |

**CRITICAL:** The **Materials** column content MUST be used to fill the `rendering.material_quality` field in the JSON. This is not optional — material quality is environment-specific and deterministic.

Output: single environment name + 1-sentence justification.

Save to: `output/environment.txt`

Example:
> "Lightning/Storm — volatile electrical networks, sudden illumination revealing hidden terrain, charged atmosphere of public exposure, transformative storm systems."

---

### STEP 5 — Build Image Generation JSON

Now that all data is gathered, construct the complete image generation JSON.

This can be done via:
- **Option A:** LLM API call with `src/prompts/json_builder.md` template (recommended for consistency)
- **Option B:** Templating logic in Python (faster but requires careful implementation)

Recommended: **Option A** — call LLM with the json_builder template.

```bash
python3 src/scripts/prompts.py --build-json
```

The json_builder template is comprehensive and includes:
- Material quality lookup tables
- Blend option selection logic (AI-driven)
- Contrast/lighting/color mapping rules
- Forbidden vs required language enforcement
- Brightness minimum rules
- Self-check validation

See `src/prompts/json_builder.md` for full details.

**Save to:** `output/image_prompt.json`

**Note:** The LLM selects which blend option (A/B/C) to use based on the full scene concept, with art keywords as the primary influence.

**Creature fragment phrase:** After creature selection, a single-sentence geological distillation of the creature's essence is extracted as `creature_fragment_phrase` and passed into the json_builder template. It is woven into the midground description as something a viewer might infer through pareidolia — never a literal creature form.

**CRITICAL - Also extract and save blend_option:**

After the LLM returns the complete JSON, extract the blend option selection and save it separately for use in STEP 8 (video prompt) and STEP 15 (database):

```python
import json

# Read the generated JSON
with open('output/image_prompt.json') as f:
    image_json = json.load(f)

# Extract blend option from creature_integration.blend field
# Format is: "Option A: Sculptural 100%" or "Option B: Sculptural 60-70%..." etc.
blend_full = image_json['creature_integration']['blend']
blend_option = blend_full.split(':')[0].strip()  # Extracts "Option A" / "Option B" / "Option C"

# Save to separate file for later use
with open('output/blend_option.txt', 'w') as f:
    f.write(blend_option)
```

This ensures blend_option is available downstream without having to re-parse the large JSON file.

---

### STEP 6 — Extract Card Metadata

Either as part of the json_builder LLM call or as a separate call, generate card metadata.

```bash
python3 src/scripts/prompts.py --extract-metadata
```

Template: `src/prompts/metadata_builder.md`

Input: `output/image_prompt.json`

Output: `output/card_metadata.json`

```json
{
  "title": "STATIC HOLLOW",
  "scene_description": "Charged geological formations rise through storm mist as two moons drift overhead.",
  "date_display": "01 MAR 2026"
}
```

---

### STEP 7 — Generate Art Image

Invoke your configured image generation system with `output/image_prompt.json` as the generation prompt.

**CRITICAL:** The `avoid` array items MUST be passed as negative prompts to the image generator (if supported), not just listed in positive text.

```bash
# Construct positive prompt from image_prompt.json (exclude the avoid array)
POSITIVE_PROMPT=$(python3 -c "import json; data=json.load(open('output/image_prompt.json')); print(json.dumps(data))")

# Extract avoid items as negative prompt string
NEGATIVE_PROMPT=$(python3 -c "import json; data=json.load(open('output/image_prompt.json')); print(', '.join(data['rendering']['avoid']))")

# Call image generator (example using CLI - adapt to your system)
python3 src/scripts/image_gen.py \
  --prompt "$POSITIVE_PROMPT" \
  --negative "$NEGATIVE_PROMPT" \
  --aspect-ratio 3:4 \
  --output output/art_raw.png
```

If generation fails, retry once. If still failing, stop and report.

---

### BRANCH POINT — Mode Check After Prompts

Read `PIPELINE_MODE` and branch:

---

#### IF `PIPELINE_MODE=automatic`
Continue directly to Step 8 with no Telegram interaction.

---

#### IF `PIPELINE_MODE=telegram`

Send both prompts to Telegram:

```python
# Send output/image_prompt.json
# Send output/video_prompt.txt
# Include run token + deadline in message
```

Wait for you to send BOTH manual outputs back (prefer Telegram documents):
- Generated image (`.png` / `.jpg`)
- Generated video (`.mp4`)

When both files are received before deadline:
- Save to `output/generated_art.png` and `output/generated_video.mp4`
- Skip API generation steps
- Continue to Step 10

If both files are NOT received before `PIPELINE_MANUAL_DEADLINE_LOCAL`:
- Trigger automatic image+video generation via API
- Continue pipeline normally from Step 10 onward

---

### STEP 8 — Build Video Animation Prompt

Template: `src/prompts/video.md`

Fill with: environment, depth level, recovery zone, matrix body keywords, matrix art keywords, one-liner, blend option, energy zone.

Call LLM or use templating logic. Save to `output/video_prompt.txt`.

See `src/prompts/video.md` for the full template structure with art keyword mappings for camera movement and motion quality.

**Materiality guardrails:** After the LLM generates the video prompt, `prompts.py` validates that environment motion is physics-correct for the material class (solid environments fracture/shed debris; atmospheric environments fail through pressure/compression; fluid environments surge/billow). If violated, the LLM is given a correction prompt and retried up to 3 times.

---

### STEP 9 — Generate Video

*(Skip if `PIPELINE_MODE=telegram` and manual video is already received before deadline)*

Call your configured video generation system with:
- Input: `output/art_raw.png` (base image)
- Prompt: `output/video_prompt.txt`
- Duration: 8 seconds (single-take, not looping)
- Format: MP4, 1080×1920, 9:16

Save to: `output/card_final_video_raw.mp4`

Retry once on failure. Stop and report if still failing.

---

### STEP 10 — Render Final Card via composite.py

Apply the card frame overlay to both the static image and the video, following the Figma V4.5 spec exactly.

#### 10a — Render PNG card

```bash
python3 src/scripts/composite.py \
  --art output/art_raw.png \
  --data output/daily_data.json \
  --metadata output/card_metadata.json \
  --output output/card_final.png
```

#### 10b — Render video card

```bash
python3 src/scripts/composite.py \
  --art output/card_final_video_raw.mp4 \
  --data output/daily_data.json \
  --metadata output/card_metadata.json \
  --output output/card_final.mp4 \
  --format video
```

#### What composite.py applies (Figma V4.5 spec)

- White background (1080×1920)
- Top strip: date box left-aligned with border / three circular arc indicators centered / logo mark (spark icon) right-aligned
  - Date format: `DD MMM YYYY` — day as 2-digit number, month as 3-character uppercase abbreviation, year as 4-digit number (e.g. `01 MAR 2026`)
  - Arc 1: Sleep Score % on 0–100 scale
  - Arc 2: Recovery % on 0–100 scale
  - Arc 3: Strain on 0–21 scale
- Title text below top strip: bold, uppercase, dynamic font sizing for long names (max 13 chars)
- Art image with chamfered top-right corner
- Scene description below art: text wrapping, max 2 lines

**If composite fails on either file, stop and report. Do not proceed to upload.**

---

### STEP 11 — Generate Grid Thumbnail (4:5)

Separate 1080×1350 version passed as `cover_url` to Instagram API. This gives full control over profile grid appearance without relying on Instagram's auto-crop.

```bash
python3 src/scripts/composite.py \
  --art output/art_raw.png \
  --data output/daily_data.json \
  --metadata output/card_metadata.json \
  --output output/card_thumbnail.png \
  --format grid
```

Output: `output/card_thumbnail.png` (1080×1350, 4:5 aspect ratio)

---

### STEP 12 — Upload to VPS

```bash
DATE_SLUG=$(date +%Y-%m-%d)

cp output/card_final.mp4 /var/www/media/whoop-card-${DATE_SLUG}.mp4
cp output/card_thumbnail.png /var/www/media/whoop-card-${DATE_SLUG}-thumb.png

VIDEO_URL="${VPS_PUBLIC_BASE_URL}/whoop-card-${DATE_SLUG}.mp4"
THUMB_URL="${VPS_PUBLIC_BASE_URL}/whoop-card-${DATE_SLUG}-thumb.png"
```

**Verify both URLs return HTTP 200 before continuing.** Instagram will silently fail if files are not publicly accessible. Stop and report if either URL fails.

---

### STEP 13 — Build Instagram Caption

```python
import json

with open('output/card_metadata.json') as f:
    metadata = json.load(f)
with open('output/daily_data.json') as f:
    data = json.load(f)

caption = f"""{metadata['title']}

{metadata['scene_description']}

Strain {data['strain']} • Recovery {data['recovery_pct']}% • Sleep {data['sleep_hours']}h

#WHOOPCard #BiometricArt #VedicAstrology #DailyCard #GenerativeArt #RetroSciFi #AbstractLandscape
"""

with open('output/caption.txt', 'w') as f:
    f.write(caption)
```

**Caption format:**
```
STATIC HOLLOW

Charged geological formations rise through storm mist as two moons drift overhead.

Self-expression through uncomfortable gains — creative ambition channeled through networks that don't feel natural. Career obsession meets emotional public presence, amplifying unconventional paths toward recognition.

Strain 14.56 • Recovery 51% • Sleep 7.08h

#WHOOPCard #BiometricArt #VedicAstrology #DailyCard #GenerativeArt #RetroSciFi #AbstractLandscape
```

---

### STEP 14 — Post to Instagram

#### 14a — Create media container

```bash
curl -X POST "https://graph.facebook.com/${INSTAGRAM_GRAPH_API_VERSION:-v25.0}/${INSTAGRAM_USER_ID}/media" \
  -d "media_type=REELS" \
  -d "video_url=${VIDEO_URL}" \
  -d "cover_url=${THUMB_URL}" \
  -d "caption=$(python3 -c 'import urllib.parse; print(urllib.parse.quote(open("output/caption.txt").read()))')" \
  -d "share_to_feed=true" \
  -d "access_token=${INSTAGRAM_ACCESS_TOKEN}"
```

Save returned `id` to `output/instagram_creation_id.txt`.

#### 14b — Poll for processing

```bash
CREATION_ID=$(cat output/instagram_creation_id.txt)
for i in $(seq 1 30); do
  STATUS=$(curl -s "https://graph.facebook.com/${INSTAGRAM_GRAPH_API_VERSION:-v25.0}/${CREATION_ID}?fields=status_code,status&access_token=${INSTAGRAM_ACCESS_TOKEN}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status_code','UNKNOWN'))")
  echo "Poll ${i}: ${STATUS}"
  if [ "$STATUS" = "FINISHED" ]; then break; fi
  if [ "$STATUS" = "ERROR" ]; then echo "ERROR: Instagram processing failed"; exit 1; fi
  sleep 10
done
```

Polls every 10 seconds, max 5 minutes. Stop and report if FINISHED never reached.

#### 14c — Publish

```bash
PUBLISH_RESPONSE=$(curl -s -X POST "https://graph.facebook.com/${INSTAGRAM_GRAPH_API_VERSION:-v25.0}/${INSTAGRAM_USER_ID}/media_publish" \
  -d "creation_id=${CREATION_ID}" \
  -d "access_token=${INSTAGRAM_ACCESS_TOKEN}")

INSTAGRAM_POST_ID=$(echo "$PUBLISH_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))")
echo "$INSTAGRAM_POST_ID" > output/instagram_post_id.txt
```

---

### STEP 15 — Archive, Log, and Database

```bash
DATE_SLUG=$(date +%Y-%m-%d)

TITLE=$(python3 -c "import json; print(json.load(open('output/card_metadata.json'))['title'])")
echo "$(date): [SUCCESS] ${TITLE} posted" >> pipeline.log
```

#### Card Database (SQLite)

**Schema:** `database/cards.db`

Two tables are maintained:

- **`cards`** — one row per day, full card payload including Instagram post ID
- **`environment_history`** — one row per day, tracks environment selection with a `selection_stage` field (`environment_selected`, `cards_archive`, or `cards_backfill`). Used to prevent environment repeats within a 5-day window. The window is enforced regardless of whether the Instagram post succeeded — as long as an environment was selected for a date, it is excluded from the next 5 days.

```sql
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    scene_description TEXT NOT NULL,
    environment TEXT NOT NULL,
    creature TEXT NOT NULL,
    blend_option TEXT NOT NULL,
    energy_zone TEXT NOT NULL,
    recovery_pct INTEGER NOT NULL,
    sleep_score_pct INTEGER NOT NULL,
    strain REAL NOT NULL,
    sleep_hours REAL NOT NULL,
    depth_level TEXT NOT NULL,
    dasha_maha TEXT NOT NULL,
    dasha_antar TEXT NOT NULL,
    dasha_pratyantar TEXT NOT NULL,
    dasha_sookshma TEXT NOT NULL,
    dasha_prana TEXT NOT NULL,
    image_path TEXT NOT NULL,
    video_path TEXT NOT NULL,
    image_prompt_json TEXT NOT NULL,
    instagram_post_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Insert row:**

```python
import sqlite3
import json

conn = sqlite3.connect('database/cards.db')
cursor = conn.cursor()

with open('output/daily_data.json') as f:
    data = json.load(f)
with open('output/card_metadata.json') as f:
    metadata = json.load(f)
with open('output/environment.txt') as f:
    environment = f.read().split('—')[0].strip()
with open('output/creature.txt') as f:
    creature = f.read().split('—')[0].strip()
with open('output/image_prompt.json') as f:
    image_prompt_json = json.dumps(json.load(f))
with open('output/instagram_post_id.txt') as f:
    instagram_post_id = f.read().strip()
with open('output/blend_option.txt') as f:
    blend_option = f.read().strip()

cursor.execute("""
    INSERT INTO cards (
        date, title, scene_description, environment, creature, blend_option,
        energy_zone, recovery_pct, sleep_score_pct, strain, sleep_hours, depth_level,
        dasha_maha, dasha_antar, dasha_pratyantar, dasha_sookshma, dasha_prana,
        image_path, video_path, image_prompt_json, instagram_post_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    data['date'],
    metadata['title'],
    metadata['scene_description'],
    environment,
    creature,
    blend_option,
    data['energy_zone'],
    data['recovery_pct'],
    data['sleep_score_pct'],
    data['strain'],
    data['sleep_hours'],
    data['depth_level'],
    data['dasha']['maha'],
    data['dasha']['antar'],
    data['dasha']['pratyantar'],
    data['dasha']['sookshma'],
    data['dasha']['prana'],
    f"output/{data['date']}/card_final.png",
    f"output/{data['date']}/card_final.mp4",
    image_prompt_json,
    instagram_post_id
))

conn.commit()
conn.close()
```

VPS files: safe to delete after 24 hours.

---

## Error Handling

At every step: stop immediately on failure. Log to `output/error.log` with timestamp and step number. Report to user via OpenClaw with exact error and step name. Leave all `output/` files in place for debugging. Do not auto-retry beyond what is specified per step.

---

## Instagram Access Token Refresh

Token expires every 60 days. Refresh at day 50:

```bash
curl -X GET "https://graph.facebook.com/refresh_access_token" \
  -d "grant_type=ig_refresh_token" \
  -d "access_token=${INSTAGRAM_ACCESS_TOKEN}"
```

Update `INSTAGRAM_ACCESS_TOKEN` in `.env`. Set a recurring calendar reminder.

---

## Rulebook V4.5 Core Rules (Quick Reference)

- WHOOP drives 80%, Dasha seeds 20%
- THREE separate LLM API calls for dasha outputs — never combined
- Creature is always independent of energy zone and environment
- Sleep Score does double duty: depth level (spatial) AND behavior matrix input (atmospheric)
- Depth keywords are SPATIAL (where you are), art keywords are ATMOSPHERIC (how it feels)
- **Blend option A/B/C selected by LLM** when building JSON — art keywords heavily influence, full scene considered
- Blend option NOT pre-calculated, NOT mechanically derived from environment type alone
- **Data timing:** Strain from YESTERDAY, Recovery/Sleep from TODAY, Dasha for TODAY
- Brightness minimum 40% always maintained
- No anatomical language, no action verbs, no literal creature forms in any prompt
- All visual effects always subtle — vintage film quality
- `avoid` array items MUST be negative prompts in image generator (if supported), not positive text
- Material quality is deterministic from environment materials column

Full rulebook: `../STATE_ZERO_RULEBOOK.md`

---

## VERSION HISTORY

**v2.0.0 (Current)** — Comprehensive rewrite addressing:
- Fixed duplicate Step 5c labels
- **Changed blend option to AI-driven selection** (was deterministic keyword matching, now LLM chooses based on full scene)
- **Added critical data timing logic:** Strain from YESTERDAY, Recovery/Sleep from TODAY, Dasha for TODAY
- **Made LLM-agnostic:** Removed hardcoded references to Claude, NanaBananaPro, VEO3 — now generic LLM/image/video generation
- **Extracted prompt templates to separate files:** Applied hybrid documentation approach
- Added full prompt template specifications for all 6 AI prompts
- Emphasized depth keywords (spatial) vs art keywords (atmospheric) distinction
- Added explicit negative prompts deployment instruction for image generators
- Added Crystalline environment override note in blend options
- Filled TBD caption format with concrete example
- Added SQLite database schema and implementation
- Added materials column explicit mapping instruction
- Restructured Step 5 with clearer sub-steps
- Added error handling for date out of range
- Strengthened required vs forbidden language enforcement
- Added card metadata extraction as separate step

**v1.0.0** — Initial pipeline specification

---

### Deployment Readiness

For standalone deployment, ensure the following directory structure exists at the project root:

```text
/project-root/
├── .env                         # Required API keys and local config
├── docs/
│   ├── PIPELINE_SPEC.md
│   └── STATE_ZERO_RULEBOOK.md
├── src/
│   ├── assets/
│   ├── prompts/
│   └── scripts/
└── astrology_generator/
```

**Path Management:** All scripts use `src/scripts/utils.py` to automatically detect the project and private/runtime roots. This allows the pipeline to be executed from any working directory while maintaining consistent references to runtime `output/`, `database/`, `state`, and local-test `local_vps/`.

---

**END OF PIPELINE SPEC V2.0**
