# Google Flow shadow run

This branch is an isolated experiment. Production `main` keeps its current schedule, API provider, database, and Instagram post. The shadow performs its own WHOOP lookup, astrology lookup, OpenRouter prompt generation, Flow image and video generation, card rendering, and four portfolio renders. It has no publishing credentials. The shadow archive is inserted only into its own SQLite database.

## Deploy the separate service

On the **other VPS**, create a new Dokploy **Docker Compose** service from branch `codex/flow-shadow-experiment` with Compose Path `./compose.flow-shadow.yml`. Do not replace or redeploy the current production application or use its VPS. The Compose file builds `Dockerfile.flow-shadow`, keeps the container running for Dokploy Compose Schedule Jobs, gives Chrome 512 MB shared memory, and applies the browser's seccomp profile to the actual daily container. The previous local failure occurred when those Chrome settings were used only for the one-off sign-in container. Do not expose a domain, ports, or auto-deploy for this experiment. Use an `amd64` host; this Dockerfile installs Google's `amd64` Chrome package. The old production SSH key remains read-only for `/opt/state-zero-private/runtime/output/$RUN_DATE/` and is not used for this deployment.

The image pins gflow to commit `88ff5371c25551af28ab748691f70d32e33ab37c` and Playwright to 1.61.0. Google's apt repository supplies current Chrome at build time, so a rebuilt image needs the live gate repeated before use. On Apple Silicon, build with `docker build --platform linux/amd64 -f Dockerfile.flow-shadow -t state-zero-flow-shadow:local .`.
The image applies `ops/gflow-agent-panel.patch`: close the expanded Agent session panel before checking for its mode chip, then click only a confirmed pressed chip. The zero-credit preflight uses that same recovery and waits for the settings control to become visible. Closing the panel manually on the actual account revealed the standard Nano Banana 2 image controls (3:4 and one output) and Veo 3.1 Fast video controls; a fresh browser launch passed the ready gate. Auth cookies alone are still insufficient.
The composer patch also recovers an existing clip after a download failure. `ops/gflow-media-recovery.patch` handles the migrated account's thumbnail URL by opening the recorded clip route, requesting its original 720p download, and observing the application's CDN request for the matching workflow. The host allow-list, MP4 magic, and exact byte-size check remain enforced. The recovered first clip matched the manual original download's SHA-256. Recovery never resubmits generation. Run `python -m unittest discover -s tests -p test_gflow_patch.py` inside the shadow image to check these patched paths.
`ops/gflow-submit-boundary.patch` writes a private per-attempt marker immediately before the submit click. The adapter retries a transient error only when that marker is absent. It can download an already submitted media ID again, and submits a replacement Veo clip only after Flow explicitly reports generation failure. At most two video submit markers are allowed per date.
Chrome and the pipeline run as the unprivileged `shadow` user (UID 10001). Both mounted directories must be writable by UID 10001. Prepare only the new shadow directories or volumes; do not run sign-in or scheduled jobs as root.

Create two new, persistent, private host directories before deploying; set `SHADOW_PRIVATE_HOST_DIR` and `SHADOW_PROFILE_HOST_DIR` to their absolute paths in the Dokploy Compose environment. The Compose file refuses missing host paths:

| New private host directory | Container path | Contains |
| --- | --- | --- |
| `<SHADOW_PRIVATE_HOST_DIR>` | `/opt/state-zero-flow-shadow` | astrology files, WHOOP token, logs, outputs, shadow SQLite |
| `<SHADOW_PROFILE_HOST_DIR>` | `/opt/state-zero-flow-profile` | Flow Chrome cookies, gflow catalog, incidents |

Create both with owner-only permissions. Put an empty `.state-zero-flow-shadow-private` file in the private directory and an empty `.state-zero-flow-shadow-profile` file in the profile directory before starting the service. The runner requires both markers, so a missing volume fails before any generation. Never mount production runtime directories, production SQLite, or the production browser profile. The build excludes `.env` and runtime data via `.dockerignore`; the runner refuses a checkout `.env` and publishing/API credentials.

Set these environment values on the **new Compose service only**. Dokploy writes its environment to a Compose-side `.env`; `compose.flow-shadow.yml` explicitly passes only the needed variables into the container:

```text
STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-flow-shadow
GFLOW_CLI_HOME=/opt/state-zero-flow-profile
GFLOW_PROFILE=shadow
FLOW_PREFLIGHT_PROJECT_ID=<existing Flow project ID from the interactive sign-in>
FLOW_VIDEO_EXPLICIT_DURATION=false
FLOW_IMAGE_UPSCALE_2K=false
FLOW_VIDEO_UPSCALE_1080P=true
SHADOW_TRIAL_START_DATE=<the local date of the first full daily run, YYYY-MM-DD>
SHADOW_ALERT_BOT_TOKEN=<bot token for the existing Telegram chat, shadow-specific variable>
SHADOW_ALERT_CHAT_ID=<existing Telegram chat ID>
PIPELINE_MODE=automatic
PIPELINE_POST_TO_INSTAGRAM=false
PIPELINE_MEDIA_MODE=local_test
PORTFOLIO_MEDIA_ENABLED=true
MEDIA_GENERATION_PROVIDER=flow
FLOW_API_FALLBACK_ENABLED=false
GOOGLE_API_FALLBACK_ENABLED=false
PIPELINE_TIMEZONE=Asia/Kolkata
GFLOW_CLI_HEADLESS=false
GFLOW_CLI_HISTORY_PROMPTS=redacted
OPENROUTER_API_KEY=<separate or existing key; this experiment calls OpenRouter>
PROMPT_GOOGLE_API_KEY=<Gemini key for prompt-only fallback; never used for media>
OPENROUTER_CALL_DEADLINE_SECONDS=200
WHOOP_CLIENT_ID=<shadow OAuth app ID>
WHOOP_CLIENT_SECRET=<shadow OAuth app secret>
```

The runner rejects `INSTAGRAM_*`, `VPS_*`, `TELEGRAM_*`, and `GOOGLE_API_KEY_*` values. Use only the two `SHADOW_ALERT_*` values for Telegram; the pipeline's normal Telegram variables must remain absent. Do not mount SSH keys. Keep the service's environment and volume access restricted. Disable Docker/Dokploy automatic command retries. The runner's atomic per-date marker independently blocks repeated attempts, including failures and concurrent invocations. It refuses dates outside the seven-day window.

`PROMPT_GOOGLE_API_KEY` is for Gemini prompt fallback only; it does not enable API media generation. The runner requires this key and the 200-second OpenRouter deadline. A timed-out prompt call is retried once on OpenRouter; a second timeout goes to Gemini. Other OpenRouter errors go directly to Gemini. Run only one Chrome or gflow process against the persistent profile at a time. When moving the same profile volume between containers, verify the previous Chrome process is gone before clearing stale `Singleton*` links.

Copy only `natal.yaml` and `dasha_periods.yaml` into `$STATE_ZERO_PRIVATE_ROOT/astrology/`. The shadow WHOOP grant must be separate because WHOOP rotates refresh tokens. Prefer a separate WHOOP developer app with `http://localhost:8888/callback`; authorize the same WHOOP user through that app. The existing `ops/auth_whoop.py` writes to `$STATE_ZERO_PRIVATE_ROOT/runtime/state/whoop_token_state.json`. Run it in a one-off container on the Linux server with the shadow private mount, `--network host`, and only the shadow WHOOP environment. Tunnel `localhost:8888` over SSH to complete its callback in your local browser. Inspect that the token file landed **only** in the shadow volume. Never copy the production refresh token.

For a Docker host, the one-off authorization command is:

```sh
docker run --rm -it --network host \
  --env-file <SHADOW_WHOOP_ENV_FILE> \
  -e STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-flow-shadow \
  -v <SHADOW_PRIVATE_HOST_DIR>:/opt/state-zero-flow-shadow \
  --entrypoint python3 <SHADOW_IMAGE> /app/ops/auth_whoop.py
```

The environment file needs only the shadow `WHOOP_CLIENT_ID` and `WHOOP_CLIENT_SECRET`. Keep it outside the repository and readable only by the owner.

## Interactive sign-in and first live gate

For local Docker Desktop, allocate at least 4 GB RAM to its Linux VM. The login desktop uses 1920×1080 to match the CLI browser window; use Screen Sharing’s Scale to Fit when needed.

Authenticate Flow **inside a one-off Linux container** with the persistent shadow profile volume. Run headed Chrome with `DISPLAY=:99`, `Xvfb :99`, and `gflow auth login --browser chrome --profile shadow`. The one-off login container needs `--security-opt seccomp=<absolute path to ops/flow_chrome_seccomp.json>` because gflow enables Chrome's sandbox during sign-in; the file is the [Playwright 1.61.0 profile](https://github.com/microsoft/playwright/blob/v1.61.0/utils/docker/seccomp_profile.json), which allows Chrome's user namespace calls. A temporary password-protected `x11vnc` process shows the display on host loopback only. Use an SSH tunnel when remote, or open `vnc://127.0.0.1:5900` on the Mac for a local container; `localhost` may resolve to IPv6 while Docker publishes IPv4 only. Enter the temporary VNC password in Screen Sharing; enter the Google password only inside Google's page in Chrome. Never expose VNC publicly or copy the failed Mac profile. `gflow auth status` is useful but does not pass the gate by itself: the actual project editor must open after Chrome restarts. [Upstream container notes](https://github.com/ffroliva/gflow-cli/blob/88ff5371c25551af28ab748691f70d32e33ab37c/docker/README.md), [authentication notes](https://github.com/ffroliva/gflow-cli/blob/88ff5371c25551af28ab748691f70d32e33ab37c/docs/AUTHENTICATION.md).

```sh
docker run --rm -it --platform linux/amd64 --shm-size=512m \
  --security-opt seccomp=<ABSOLUTE_PATH_TO_FLOW_CHROME_SECCOMP_JSON> \
  -p 127.0.0.1:5900:5900 \
  -e VNC_PASS=<TEMPORARY_EIGHT_CHARACTER_VNC_PASSWORD> \
  -v <SHADOW_PROFILE_HOST_DIR>:/opt/state-zero-flow-profile \
  --entrypoint sh <SHADOW_IMAGE> -lc \
  'Xvfb :99 -screen 0 1920x1080x24 & sleep 1; x11vnc -storepasswd "$VNC_PASS" /tmp/vncpass >/dev/null 2>&1; x11vnc -display :99 -listen 0.0.0.0 -rfbport 5900 -rfbauth /tmp/vncpass -forever -shared -input KMBC & DISPLAY=:99 gflow auth login --browser chrome --profile shadow'
```

Open an SSH tunnel to the server's loopback port 5900 and connect your VNC viewer to `localhost:5900` while that temporary command runs.

On the tested account, Veo Fast refused explicit `--duration 8` before submission because its settings have no duration row. Omit that flag and require the downloaded clip to measure eight seconds; the adapter validates this. The first uploaded start frame may show a one-time “Rights to use this image” agreement. Complete that interactive gate before scheduling: the CLI intentionally does not accept the account owner's rights declaration automatically. A timed-out upload is not a submitted video; reconcile the CLI operation record before any manual continuation.

For the first local gate, use **two new Docker volumes** rather than the old Mac profile or production private directory. Initialise only those volumes with the required marker files and UID 10001 ownership in a one-off root container; the browser and scheduled job then run as `shadow`. During sign-in, follow any `/about` identity check through its main button, open an existing Flow project, and record its `/project/<id>` as `FLOW_PREFLIGHT_PROJECT_ID`. Close the Agent panel and use the standard view if offered. Do not submit a prompt.

```sh
docker volume create state-zero-flow-shadow-private-v2
docker volume create state-zero-flow-shadow-profile-v2
docker run --rm --platform linux/amd64 --user root \
  -v state-zero-flow-shadow-private-v2:/opt/state-zero-flow-shadow \
  -v state-zero-flow-shadow-profile-v2:/opt/state-zero-flow-profile \
  --entrypoint sh state-zero-flow-shadow:local -lc \
  'touch /opt/state-zero-flow-shadow/.state-zero-flow-shadow-private /opt/state-zero-flow-profile/.state-zero-flow-shadow-profile && chown -R 10001:42 /opt/state-zero-flow-shadow /opt/state-zero-flow-profile && chmod 700 /opt/state-zero-flow-shadow /opt/state-zero-flow-profile'
```

Mount `state-zero-flow-shadow-profile-v2` for the local login command above; supply the same seccomp profile from this checkout. Mount both volumes for the preflight and trial. These names intentionally differ from any earlier local attempt.

With the login window closed, run `xvfb-run -a python3 /app/ops/flow_shadow_preflight.py` using that same profile volume and project ID. Repeat in a fresh container. Both checks must return `ready`; the short private status is in `$STATE_ZERO_PRIVATE_ROOT/runtime/state/flow_shadow/preflight.json`. The preflight selects and reads back the Nano Banana 2 / 3:4 / one image and Veo 3.1 Fast / Frames / 9:16 / one video controls. It types no prompt, uploads no frame, creates no project, and submits nothing. `agent_toggle_on`, `agent_panel_only`, `controls_unavailable`, or `unsupported_agent_view` stops the experiment until the standard controls are demonstrated.

Before generating a new clip, test `ops/flow_video_upscale.py` against one of the two **existing** Flow clip IDs recorded in the local review files. Run it under `xvfb-run` with the signed-in profile, the project ID, and a path in the new shadow private volume. It clicks Download → 1080p only if that option is visible. A status of `not_offered` is expected after Pro access ends; `download_unavailable` needs visual inspection before scheduling. No generation button is used. Validate the resulting file with `ffprobe` for 1080×1920, eight seconds, and audio. The trial adapter repeats this attempt per clip and falls back to local 720p scaling when it cannot validate a Flow 1080p file.

Before adding a schedule, invoke the new Compose service's command once:

```text
xvfb-run -a python3 -u /app/ops/flow_shadow_run.py
```

The runner claims the day's attempt marker, then repeats the zero-credit preflight. A failed preflight records that date as failed and sends a sign-in alert; it does not generate. When it passes, the run requests one `nano2` 3:4 image and one `veo-fast` 9:16 image-to-video clip using the local 1080×1920 black-padded frame and `--count 1`. This account has no duration control, so the command omits `--duration`; the adapter still rejects a clip outside 7.5–8.5 seconds. **Do not blindly resubmit an uncertain generation.**

For the smoke result, inspect `$STATE_ZERO_PRIVATE_ROOT/runtime/output/YYYY-MM-DD/shadow_report.json` and the private `shadow_pipeline.log`, then view:

- The original Flow image (`flow_art.png` or `flow_art.jpg`) and `generated_art.png`; check actual dimensions, sharpness, 3:4 framing, and watermark.
- `flow_start_frame.png`, `flow_raw_video.mp4`, optional `flow_1080p_download.mp4`, and `generated_video.mp4`; check start-frame binding, portrait dimensions, eight-second duration, motion, audio, and watermark. The optional 1080p download is selected only when Flow's existing clip page offers it, and accepted only if its dimensions, duration, and audio validate. Otherwise FFmpeg scales the 720p original; this changes pixel dimensions only.
- `card_final.png`, `card_final.mp4`, and `portfolio/{light,dark}.{webp,mp4}`; inspect all layouts and the visible watermark in context.
- Flow project/media IDs in the report; `flow_credits_before`, `flow_credits_after`, and observed change when `gflow credits user` works. On migrated accounts its credits endpoint may return 401, so record the Flow UI balance manually before/after. Nano Banana images use a separate quota from Veo credits.

The CLI's 2K image upscale is conditional on `FLOW_IMAGE_UPSCALE_2K=true`. It is off for this migrated account because both earlier images were 896×1200 and no usable 2K export was available. The original remains and the report marks `flow_2k_upscaled=false`. Do not mistake local resizing for restored detail. If the original is too soft in the card, reject this replacement or change the image source deliberately.

## Seven-day schedule and review

### Local gate evidence — September 27

Two archived days (2026-06-05 and 2026-04-25) produced an image, one video, full cards, and all four portfolio files in isolated local Docker volumes. These replayed archived inputs and prompts; they did not exercise fresh WHOOP OAuth or the full unattended daily lookup. Login survived browser restart, and the preflight recovered an expanded Agent panel. Both images were 896×1200; genuine 2K export was unavailable. Both original clips were 720×1280 with audio and measured exactly eight seconds; canonical cards were 1080×1920 after local scaling.

The second clip's automatic download succeeded, but the CLI's final rename crossed from its container filesystem into the private volume and raised `EXDEV`. Its recorded error hash exactly matched that reproduced exception. The adapter now passes `--out-dir` alongside `--output`, keeping download and rename on the same volume. Original recovery was verified without generating another clip, and the corrected relocation was checked using an existing file. No additional paid generation was used to retest this path.

Private review copies and hash/dimension reports are under `/Users/harshit/Projects/state-zero-flow-linux-review/`. Credit lookup still uses the old Labs authentication endpoint on this account, so no balance change was measured; 20 credits per video remains an estimate. At this point the separate WHOOP authorization, server shadow service, and seven scheduled runs were still pending. Production was untouched.

### Fresh local gate — September 28

The first full local run failed during prompts because the old OpenRouter key returned 401 and the shadow had no usable Gemini prompt fallback. A manually isolated retry with a disposable OpenRouter key stalled on a later prompt call. Both stopped before Flow submission. The branch now has a prompt-only Gemini fallback, a 200-second deadline per OpenRouter attempt, and one retry on timeout. A second manual retry completed the fresh WHOOP lookup, prompts, one Nano Banana 2 image, one Veo Fast start-frame video, cards, portfolio variants, and private SQLite archive. The image original is 896×1200. The raw clip is 720×1280, eight seconds, with audio; Flow's genuine 1080×1920 download was offered, validated, and selected as `generated_video.mp4`. Both media steps used Flow with one submission and zero retries. Credit balance lookup remained unavailable, so 20 video credits is an estimate. Reviewable media is in `/Users/harshit/Projects/state-zero-flow-linux-review/2026-09-28/`; sensitive inputs and logs remain in the separate local shadow volume. The timeout-to-Gemini path was checked without live billing; the successful run used OpenRouter throughout. This local gate does not count toward the seven scheduled VPS dates.

The original September 28 portfolio MP4s had unknown colour tags. Zero-credit re-exports under `2026-09-28/color-tag-review/portfolio/` now report `tv,bt709,iec61966-2-1,bt709` for both themes. The final frame overlay also composites in RGB before YUV encoding: FFmpeg decodes sampled dark-frame pixels as `(13,13,13)`, matching the `#0D0D0D` still frame. A local macOS browser canvas sample of the playing dark MP4 read `#0D0D0D` beside a `#0D0D0D` page background. The eventual portfolio page remains a separate visual gate.

After the first **fresh WHOOP** full run validates, create one Dokploy Compose Schedule Job on the new service with command `xvfb-run -a python3 -u /app/ops/flow_shadow_run.py`. Set it to **15:15 Asia/Kolkata** after confirming the scheduler's timezone on that VPS (09:45 UTC if its cron is UTC). Do not change `COMPOSE_PROJECT_NAME`; Dokploy uses it to identify the job's container. Keep the production schedule as is. The first fresh full run is day one; the two archived local days do not count. Each of the next six dates gets one attempt. A failure remains in the seven-day evidence. Reconcile its Flow media ID and credits before any manual intervention; never automatically delete a date marker. On day seven the runner sends a Telegram summary, and subsequent scheduled invocations generate nothing. Review at least three dates after the account actually loses Pro access.

Inspect seven scheduled runs, including at least three after the account actually loses Pro access. Compare each day's image, video, full card, and portfolio variants with the current API results. The pass criteria are correct image-to-video binding, one usable clip per day with at most two submitted attempts, usable image sharpness, acceptable watermark, correct 1080×1920 card and smaller portfolio layouts, successful downloads and private archive, and no production posting or data changes. A failed date remains a trial finding and prevents acceptance until fixed and retested. Keep the report, artifacts, and private logs for each date; do not send prompts, WHOOP data, cookies, or signed media URLs to ordinary logs.

Only after review should the provider change be merged with `google_api` still the default. A separate later production configuration can choose `flow` and `FLOW_API_FALLBACK_ENABLED=true`; that switch uses the existing Google API step for the failed media stage, while `GOOGLE_API_FALLBACK_ENABLED` continues to mean the secondary Google API key. Keep the former API provider configuration ready for immediate rollback.
