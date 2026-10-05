# Google Flow shadow service

The shadow runs WHOOP lookup, astrology lookup, prompt generation, Flow image
and video generation, card rendering, and portfolio rendering in an isolated
service. Outputs and the SQLite archive stay private. Instagram posting, public
media delivery, and API media fallback are disabled.

## Build and storage

Create a Dokploy Compose service from `codex/flow-shadow-experiment` using
`compose.flow-shadow.yml`. It builds `Dockerfile.flow-shadow` for `linux/arm64`
with headed Google Chrome, Playwright 1.61.0, and gflow commit
`88ff5371c25551af28ab748691f70d32e33ab37c`. The container runs as UID 10001,
with a 4 GiB memory limit, 512 MiB shared memory, and the browser seccomp profile.
It stays idle between schedule jobs; no public domain or port is needed.

Prepare two private directories owned by UID 10001 with owner-only permissions:

| Compose variable | Container mount | Contents |
| --- | --- | --- |
| `SHADOW_PRIVATE_HOST_DIR` | `/opt/state-zero-flow-shadow` | inputs, WHOOP tokens, outputs, logs, SQLite |
| `SHADOW_PROFILE_HOST_DIR` | `/opt/state-zero-flow-profile` | persistent Chrome profile and gflow catalog |

Create `.state-zero-flow-shadow-private` in the private directory and
`.state-zero-flow-shadow-profile` in the profile directory. The runner requires
both markers. Compose refuses to create missing host directories. Keep the mounts
separate from the repository and any production private storage or browser profile.

Copy only the required `natal.yaml` and `dasha_periods.yaml` into the private
mount's `astrology/` directory. All copied files must be readable by UID 10001.
Keep credentials, personal inputs, generated media, and browser state outside Git.

For a local ARM64 build:

```sh
docker build --platform linux/arm64 -f Dockerfile.flow-shadow \
  -t state-zero-flow-shadow:local .
```

Chrome is installed from its APT repository at build time. Repeat the browser and
editor checks after rebuilding or changing the pinned CLI or browser dependencies.
Verify `aarch64`, the Chrome executable, and sufficient host memory before use.

## Environment

Enter the following in the private Compose environment. Host paths must be
absolute; secrets and account identifiers must not be committed:

```text
SHADOW_PRIVATE_HOST_DIR=<private host directory>
SHADOW_PROFILE_HOST_DIR=<profile host directory>
FLOW_PREFLIGHT_PROJECT_ID=<existing Flow project ID>
SHADOW_TRIAL_START_DATE=<first pipeline date, YYYY-MM-DD>
OPENROUTER_API_KEY=<prompt provider key>
PROMPT_GOOGLE_API_KEY=<Gemini prompt fallback key>
SHADOW_ALERT_BOT_TOKEN=<Telegram bot token>
SHADOW_ALERT_CHAT_ID=<Telegram chat ID>
WHOOP_CLIENT_ID=<separate OAuth app ID>
WHOOP_CLIENT_SECRET=<separate OAuth app secret>
```

Compose passes only the required variables into the container. The image supplies
these fixed shadow settings:

- `PIPELINE_MODE=automatic`, `PIPELINE_POST_TO_INSTAGRAM=false`.
- `PIPELINE_MEDIA_MODE=local_test`, `PORTFOLIO_MEDIA_ENABLED=true`.
- `MEDIA_GENERATION_PROVIDER=flow`, `FLOW_API_FALLBACK_ENABLED=false`.
- `GOOGLE_API_FALLBACK_ENABLED=false`, `PIPELINE_TIMEZONE=Asia/Kolkata`.
- `STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-flow-shadow`.
- `GFLOW_CLI_HOME=/opt/state-zero-flow-profile`, `GFLOW_PROFILE=shadow`.
- `GFLOW_CLI_HEADLESS=false`, `GFLOW_CLI_HISTORY_PROMPTS=redacted`.
- `OPENROUTER_CALL_DEADLINE_SECONDS=200` (set by Compose).

The runner rejects a checkout `.env` and nonempty `INSTAGRAM_*`, `VPS_*`,
`TELEGRAM_*`, or `GOOGLE_API_KEY_*` variables. Telegram alerts use only
`SHADOW_ALERT_*`. `PROMPT_GOOGLE_API_KEY` enables prompt fallback, not media
fallback. OpenRouter timeouts get one retry before Gemini prompt fallback.

## WHOOP authorization

Use a separate OAuth grant and rotating token state for the shadow service;
do not copy a production refresh token. Configure the OAuth application's
callback as `http://localhost:8888/callback`.

Run the existing authorizer with the private mount and an owner-readable
environment file containing only the shadow WHOOP client ID and secret:

```sh
docker run --rm -it --network host \
  --env-file <SHADOW_WHOOP_ENV_FILE> \
  -e STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-flow-shadow \
  -v <SHADOW_PRIVATE_HOST_DIR>:/opt/state-zero-flow-shadow \
  --entrypoint python3 <SHADOW_IMAGE> /app/ops/auth_whoop.py
```

For a remote host, tunnel the callback port over SSH. The authorizer stores
credentials at `runtime/state/whoop_token_state.json` inside the private mount.
Verify its ownership and location without printing its contents.

## Flow sign-in and editor checks

Authenticate inside the Linux container using the persistent profile mount,
headed Chrome, and the same seccomp profile as the daily service. Run only one
browser against the profile at a time. Its
[persistent storage](https://github.com/microsoft/playwright/blob/v1.61.0/docs/src/api/class-browsertype.md)
contains cookies and local storage; a saved profile does not guarantee that Google
will retain the session indefinitely.

A temporary login desktop can be started with:

```sh
docker run --rm -it --platform linux/arm64 --shm-size=512m \
  --security-opt seccomp=<ABSOLUTE_PATH_TO_FLOW_CHROME_SECCOMP_JSON> \
  -p 127.0.0.1:5900:5900 \
  -e VNC_PASS=<TEMPORARY_EIGHT_CHARACTER_VNC_PASSWORD> \
  -v <SHADOW_PROFILE_HOST_DIR>:/opt/state-zero-flow-profile \
  --entrypoint sh <SHADOW_IMAGE> -lc \
  'Xvfb :99 -screen 0 1920x1080x24 & sleep 1; x11vnc -storepasswd "$VNC_PASS" /tmp/vncpass >/dev/null 2>&1; x11vnc -display :99 -listen 0.0.0.0 -rfbport 5900 -rfbauth /tmp/vncpass -forever -shared -input KMBC & DISPLAY=:99 gflow auth login --browser chrome --profile shadow'
```

Use an SSH tunnel for remote VNC access; never expose it publicly. Connect the
viewer to `127.0.0.1:5900` and scale the desktop to fit its window. Enter Google
credentials only on Google's sign-in page. Open a project, switch to the standard
composer if needed, and store its ID as `FLOW_PREFLIGHT_PROJECT_ID`. Close the
login browser before running preflight.

Run this in the configured shadow container, then repeat after a fresh browser
launch:

```text
xvfb-run -a python3 /app/ops/flow_shadow_preflight.py
```

Both checks must report `ready`. The preflight verifies standard image and video
settings without typing a prompt, uploading a frame, or submitting generation.
Its private status is `runtime/state/flow_shadow/preflight.json`. Cookie counts
or `gflow auth status` alone do not pass this gate. If Google requires sign-in or
an identity check, reopen the private desktop and repeat preflight afterward.

Complete any first-upload rights declaration interactively before scheduling.
The CLI does not accept that declaration on the account owner's behalf.

## First full run and media validation

With authentication ready, run one supervised date in the existing service:

```text
xvfb-run -a python3 -u /app/ops/flow_shadow_run.py --manual
```

`--manual` bypasses only the schedule clock gate. WHOOP readiness, editor
preflight, isolation checks, and the one-attempt-per-date claim remain enforced.
The image request is Nano Banana 2, 3:4, one output. The video request is Veo Fast,
9:16, one output, using a local 1080×1920 black-padded start frame.

The default `FLOW_VIDEO_EXPLICIT_DURATION=false` omits a duration control that
may be unavailable; the downloaded clip must still measure 7.5–8.5 seconds.
`FLOW_VIDEO_UPSCALE_1080P=true` attempts Download → 1080p on the existing clip.
Upgrade-only options are skipped. The file is accepted only after validating
1080×1920 dimensions, duration, and audio against the original. Otherwise the
720p original is scaled locally to 1080×1920; scaling adds no source detail.
No 4K download is requested.

`FLOW_IMAGE_UPSCALE_2K` defaults to `false`; enable it only after verifying the
account offers a usable export. Preserve the original and inspect its actual
resolution, sharpness, watermark, and crop rather than assuming an export size.

Before enabling schedules, inspect the date directory's `shadow_report.json`,
private logs, and these assets:

- Original Flow image and `generated_art.png`.
- `flow_start_frame.png`, `flow_raw_video.mp4`, optional
  `flow_1080p_download.mp4`, and `generated_video.mp4`.
- `card_final.png`, `card_final.mp4`, and `portfolio/{light,dark}.{webp,mp4}`.
- The matching private archive record, media IDs, submission counts, hashes,
  dimensions, duration, audio, credit evidence, and memory/OOM measurements.

Check start-frame binding and all finished layouts visually. Credit lookup may
be unavailable; keep observed balances distinct from submission-based estimates.

## Daily schedules

Create two Dokploy Compose Schedule Jobs targeting `flow-shadow`. Prepare them
disabled, validate the first full run, then enable both. Preserve Dokploy's
`COMPOSE_PROJECT_NAME`; jobs execute in the existing running container.

| Job | Command | Asia/Kolkata cron | UTC cron |
| --- | --- | --- | --- |
| Daily Window | `xvfb-run -a python3 -u /app/ops/flow_shadow_run.py` | `0,30 10-15 * * *` | `0,30 4-9 * * *` |
| Final Check | `xvfb-run -a python3 -u /app/ops/flow_shadow_run.py --final-check` | `15 15 * * *` | `45 9 * * *` |

Select the expression for the scheduler's actual timezone and verify its next
execution. The runner permits window checks only from 10:00 through 15:00 IST;
the cron's extra boundary tick exits without network calls. Final checks are
accepted from 15:15 through 15:44 IST. No Telegram manual-image job is needed.

Each check requires scored sleep, matching recovery, and a completed prior strain
cycle. Pending data and temporary WHOOP errors remain retryable without opening
Flow or claiming generation. The final check records a missed date and alerts
once if data is still unavailable. An execution lock serializes token refresh
and browser operations.

Once WHOOP is ready, the date is claimed before preflight and pipeline execution.
Complete, failed, or uncertain claims block later full runs for that date. Do not
delete a claim to force another attempt. Disable automatic command retries in
the scheduler. A seven-day summary is sent once, including failed or missing
dates; generation continues on day eight and afterward.

## Failure recovery

| Condition | Action |
| --- | --- |
| Sign-in required or standard controls unavailable | Stop before generation and send an actionable alert. |
| Transient failure proven to precede submission | Retry once with a fresh browser. |
| Submitted media is pending or download failed | Recover the recorded media ID; never regenerate to solve a download error. |
| Veo explicitly failed without a policy/quota rejection | Permit one replacement; at most two video submissions per date. |
| Unknown submission state, rejection, quota, or second failure | Preserve evidence, alert, and stop; no blind resubmission or API media fallback. |

The image applies the patches in `ops/gflow-*.patch` for Agent-panel recovery,
submission markers, migrated video records, and original-download recovery.
Downloads validate their origin and file contents. The adapter places temporary
and final downloads on the same volume to avoid cross-filesystem rename errors.

Keep diagnostic logs, prompts, health inputs, cookies, signed URLs, and account
identifiers private. Review the first scheduled completion separately from any
manual recovery. Compare actual peak memory and OOM events with host headroom
before changing hosting or enabling production publishing.

A later production cutover is separate from this shadow setup. The pipeline's
API provider remains the default; media fallback and publishing require explicit
production configuration. Browser automation and Google's account/UI changes
remain operational risks that require monitoring.
