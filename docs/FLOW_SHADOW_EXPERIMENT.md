# Google Flow shadow run

This branch is an isolated experiment. Production `main` keeps its current schedule, API provider, database, and Instagram post. The shadow performs its own WHOOP lookup, astrology lookup, OpenRouter prompt generation, Flow image and video generation, card rendering, and four portfolio renders. It has no publishing credentials. The shadow archive is inserted only into its own SQLite database.

## Deploy the separate service

Create a **new Dokploy application** from branch `codex/flow-shadow-experiment`, using `Dockerfile.flow-shadow`. Do not replace or redeploy the current application. The image's `sleep infinity` command keeps the container running because Dokploy Application Schedule Jobs execute commands inside a running application container. Do not expose a domain, ports, or auto-deploy for this experiment. Use an `amd64` host; this Dockerfile installs Google's `amd64` Chrome package.

The image pins gflow to commit `88ff5371c25551af28ab748691f70d32e33ab37c` and Playwright to 1.61.0. Google's apt repository supplies current Chrome at build time, so a rebuilt image needs the live gate repeated before use.

Mount two new, persistent, private host directories (or equivalent isolated volumes):

| New private host directory | Container path | Contains |
| --- | --- | --- |
| `<SHADOW_PRIVATE_HOST_DIR>` | `/opt/state-zero-flow-shadow` | astrology files, WHOOP token, logs, outputs, shadow SQLite |
| `<SHADOW_PROFILE_HOST_DIR>` | `/opt/state-zero-flow-profile` | Flow Chrome cookies, gflow catalog, incidents |

Create both with owner-only permissions. Never mount production runtime directories, production SQLite, or the production browser profile. The build excludes `.env` and runtime data via `.dockerignore`; the runner refuses a checkout `.env` and publishing/API credentials.

Set these environment values on the **new application only**:

```text
STATE_ZERO_PRIVATE_ROOT=/opt/state-zero-flow-shadow
GFLOW_CLI_HOME=/opt/state-zero-flow-profile
GFLOW_PROFILE=shadow
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
WHOOP_CLIENT_ID=<shadow OAuth app ID>
WHOOP_CLIENT_SECRET=<shadow OAuth app secret>
```

The runner rejects `INSTAGRAM_*`, `VPS_*`, `TELEGRAM_*`, and `GOOGLE_API_KEY_*` values. Do not mount SSH keys. Keep the service's environment and volume access restricted. Disable Docker/Dokploy automatic command retries. The runner's atomic per-date marker independently blocks repeated attempts, including failures and concurrent invocations.

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

## Server sign-in and first live gate

Authenticate the Flow account **inside a one-off container on the server**, mounting the shadow profile directory at `/opt/state-zero-flow-profile`. Run headed Chrome with `DISPLAY=:99`, `Xvfb :99`, and `gflow auth login --browser chrome --profile shadow`. A temporary `x11vnc` process can show that display: publish container VNC port 5900 to **host loopback only**, then use an SSH tunnel and a VNC viewer; close the temporary container and tunnel after sign-in. Never expose VNC publicly and never copy a browser profile from another machine. Verify `gflow auth status --profile shadow` in the same volume. [Upstream container notes](https://github.com/ffroliva/gflow-cli/blob/88ff5371c25551af28ab748691f70d32e33ab37c/docker/README.md), [authentication notes](https://github.com/ffroliva/gflow-cli/blob/88ff5371c25551af28ab748691f70d32e33ab37c/docs/AUTHENTICATION.md).

```sh
docker run --rm -it -p 127.0.0.1:5900:5900 \
  -v <SHADOW_PROFILE_HOST_DIR>:/opt/state-zero-flow-profile \
  --entrypoint sh <SHADOW_IMAGE> -lc \
  'Xvfb :99 -screen 0 1600x900x24 & sleep 1; x11vnc -display :99 -listen 0.0.0.0 -rfbport 5900 -nopw -forever & DISPLAY=:99 gflow auth login --browser chrome --profile shadow'
```

Open an SSH tunnel to the server's loopback port 5900 and connect your VNC viewer to `localhost:5900` while that temporary command runs.

Before adding a schedule, invoke the new application's command once:

```text
xvfb-run -a python3 -u /app/ops/flow_shadow_run.py
```

This is the controlled, credit-spending smoke test: one `nano2` 3:4 image and one `veo-fast` 9:16 image-to-video request with the local 1080×1920 black-padded frame and `--count 1`. The command requests eight seconds by default. If that account has no duration control, the CLI should refuse before submission; inspect the private gflow incident and Flow project/credits before setting `FLOW_VIDEO_EXPLICIT_DURATION=false` and manually clearing that day's marker. With the flag omitted, the adapter still rejects a clip outside 7.5–8.5 seconds. **Do not blindly resubmit an uncertain generation.**

For the smoke result, inspect `$STATE_ZERO_PRIVATE_ROOT/runtime/output/YYYY-MM-DD/shadow_report.json` and the private `shadow_pipeline.log`, then view:

- The original Flow image (`flow_art.png` or `flow_art.jpg`) and `generated_art.png`; check actual dimensions, sharpness, 3:4 framing, and watermark.
- `flow_start_frame.png`, `flow_raw_video.mp4`, and `generated_video.mp4`; check start-frame binding, portrait dimensions, eight-second duration, motion, audio, and watermark. The 720p to 1080p FFmpeg scale changes pixel dimensions only.
- `card_final.png`, `card_final.mp4`, and `portfolio/{light,dark}.{webp,mp4}`; inspect all layouts and the visible watermark in context.
- Flow project/media IDs in the report; `flow_credits_before`, `flow_credits_after`, and observed change when `gflow credits user` works. On migrated accounts its credits endpoint may return 401, so record the Flow UI balance manually before/after. Nano Banana images use a separate quota from Veo credits.

The CLI's 2K image upscale is attempted once when available. On migrated Flow accounts it may fail; the original remains and the report marks `flow_2k_upscaled=false`. Do not mistake local resizing for restored detail. If the original is too soft in the card, reject this replacement or change the image source deliberately.

## Seven-day schedule and review

After the live gate passes, create **one Dokploy Application Schedule Job** on the new application with command `xvfb-run -a python3 -u /app/ops/flow_shadow_run.py`. Set it to a staggered daily time, for example 15:15 Asia/Kolkata, after confirming the scheduler's timezone on that server. Keep the production schedule as is. Each date gets one attempt; a failure is a stop-and-investigate event. To retry manually, first reconcile the Flow project/media ID and credit change, then deliberately remove only that date's marker in the shadow state directory. Never automate marker deletion.

Inspect seven complete scheduled runs, including at least three after the Pro plan expires on September 30. Compare each day's image, video, full card, and portfolio variants with the current API results. The pass criteria are correct image-to-video binding, one clip per day, usable image sharpness, acceptable watermark, correct 1080×1920 card and smaller portfolio layouts, successful downloads and private archive, and no production posting or data changes. Keep the report, artifacts, and private logs for each date; do not send prompts, WHOOP data, cookies, or signed media URLs to ordinary logs.

Only after review should the provider change be merged with `google_api` still the default. A separate later production configuration can choose `flow` and `FLOW_API_FALLBACK_ENABLED=true`; that switch uses the existing Google API step for the failed media stage, while `GOOGLE_API_FALLBACK_ENABLED` continues to mean the secondary Google API key. Keep the former API provider configuration ready for immediate rollback.
