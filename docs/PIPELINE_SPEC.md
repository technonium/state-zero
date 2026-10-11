# State Zero daily pipeline

State Zero turns scored WHOOP data and private Vedic dasha inputs into daily
artwork, video cards, an Instagram Reel, and optional portfolio media. This
reference describes the implemented pipeline. Python 3.10+ is required; the
container uses Python 3.11.

## Execution and generation

Run `python3 src/scripts/pipeline.py` for the regular pipeline. A Flow-enabled
container runs that same command under Xvfb. Execution mode, media provider,
publishing, and delivery are independent settings:

| Setting | Values and behavior |
| --- | --- |
| `PIPELINE_MODE` | `automatic` generates media; `telegram` sends prompts and waits for manual image/video uploads, then uses automatic generation at the deadline if needed. Default: `automatic`. |
| `MEDIA_GENERATION_PROVIDER` | `google_api` (default) or `flow`; selects the automatic image/video provider, including generation after the manual deadline. |
| `PIPELINE_POST_TO_INSTAGRAM` | Defaults to `true`. `false` skips Instagram token validation, public upload and publication; prompt and media generation still run. |
| `PIPELINE_MEDIA_MODE` | `live_vps` for configured hosted delivery; `local_test` for local staging. This does not select the generation provider. |
| `PORTFOLIO_MEDIA_ENABLED` | Defaults to `false`; enables secondary light/dark still and video cards. Non-posting runs keep them private. |

The ordinary `Dockerfile` supports API generation. Flow requires headed Chrome,
Xvfb, the pinned gflow patches and a persistent authenticated profile. See the
[Flow guide](FLOW_SHADOW_EXPERIMENT.md). The isolated validation runner deliberately
rejects publishing credentials and must not be used as the production entrypoint.

Google API images use `gemini-nano-banana-2.1` by default, with 3:4 and 2K request
settings. API video defaults to `veo-3.1-fast-generate-preview`. Models can be
configured through `GOOGLE_IMAGE_MODEL` and `GOOGLE_VIDEO_MODEL`.

Flow explicitly selects Nano Banana 2.1, 3:4 and one image. Its original image
is retained; image upscaling is off by default. Flow video uses one portrait
Veo Fast start-frame generation and validates an approximately eight-second clip.
It attempts the existing clip's 1080p download, then uses local scaling of the
validated original if the upscale is unavailable or corrupt. Local scaling adds
no source detail. Submission evidence prevents blind regeneration.

## Data, prompts and outputs

The regular pipeline:

1. Claims the daily execution and validates configuration and, when publishing,
   the Instagram token.
2. Retrieves scored sleep/recovery and the completed prior strain cycle, then
   looks up the run date in the private dasha inputs.
3. Builds interpretation, creature, environment, image JSON, title, description
   and video prompts. Recent archive and selection history prevent repetition.
4. Generates media with the selected provider, or validates manual uploads.
5. Revalidates WHOOP identity/zones and renders the full image and video cards.
6. Prepares hosted media where required, builds the caption and publishes.
7. Verifies the private archive/database and runs the best-effort portfolio step.

The card date is the pipeline run date. Strain is from the completed prior cycle,
not the current open cycle. Sleep and recovery are matched to the selected sleep
and recovery cycle. A scored zero is valid; pending scores are not zero.
WHOOP selection and readiness are implemented in `lookups.py` and `whoop_client.py`.
The [rulebook](STATE_ZERO_RULEBOOK.md) and tracked prompt templates describe the
art direction; runtime validators enforce the visual and JSON restrictions.

WHOOP revalidation checks selected biometric IDs and qualitative zones before
rendering, before publication, and periodically during a Telegram wait. Numeric
drift within the same zones is recorded rather than treated as a new artwork.
A changed identity or zone stops the normal post and follows the configured
retry/emergency policy.

Keep `STATE_ZERO_PRIVATE_ROOT` outside the repository:

```text
<private-root>/
  astrology/natal.yaml
  astrology/dasha_periods.yaml
  runtime/database/cards.db
  runtime/output/YYYY-MM-DD/
  runtime/state/
```

Per-date files include `daily_data.json`, WHOOP snapshots, prompts,
`card_metadata.json`, original/generated media, `card_final.png`, `card_final.mp4`,
and diagnostics. The private database stores artwork, title/environment history,
prompts, archive paths and published post references. State files include daily
claims and durable WHOOP/Instagram token state. Preserve all of these during a
migration; copying code alone does not transfer history.

## Credentials and configuration

Use the blank slots in [`.env.example`](../.env.example). Never commit keys,
tokens, personal astrology files, health inputs, browser state or runtime output.

- `WHOOP_CLIENT_ID` and `WHOOP_CLIENT_SECRET` identify the OAuth application.
  Use `python3 ops/auth_whoop.py` to authorize it. Access/refresh environment
  values are bootstrap values; private durable token state becomes authoritative.
- `OPENROUTER_API_KEY` supplies prompt generation. Google Gemini prompt fallback
  uses the configured Google key; the isolated validation service uses its
  separate `PROMPT_GOOGLE_API_KEY` slot.
- `GOOGLE_API_KEY_PRIMARY`, `GOOGLE_API_KEY_FALLBACK` and
  `GOOGLE_API_FALLBACK_ENABLED` configure Google key selection.
  `FLOW_API_FALLBACK_ENABLED` separately permits eligible Flow-to-API media fallback.
- `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` and `TELEGRAM_BOT_TOKEN` /
  `TELEGRAM_CHAT_ID` retain their regular pipeline roles. They are not replaced
  by the isolated validation alert variables.
- `VPS_PUBLIC_BASE_URL`, `VPS_SSH_HOST`, `VPS_SSH_USER` and `VPS_SSH_PATH`
  configure hosted delivery. Use persistent private mounts and valid SSH host keys.

The default Instagram strategy is `resumable_binary`. Token handling is
validate-and-alert: `INSTAGRAM_AUTO_REFRESH_MODE=off`. Configure the optional
Meta app credentials for expiry introspection and schedule
`python3 ops/instagram_token_healthcheck.py` when using Instagram publishing.
Operators replace expiring tokens; the pipeline does not rely on automatic renewal.

## Telegram mode and scheduling

Telegram mode sends image/video prompts and accepts matching replies, run tokens,
or fresh uploads according to `PIPELINE_MANUAL_MATCH_STRICT`. Both image and
video must pass validation. Invalid files produce an actionable warning.

`PIPELINE_MANUAL_DEADLINE_MODE=run_date` uses the run date and
`PIPELINE_MANUAL_DEADLINE_LOCAL` (default `14:00`) in `PIPELINE_TIMEZONE`.
`from_now` instead uses `PIPELINE_MANUAL_WINDOW_MINUTES` for supervised work.
Posting-disabled Telegram runs still wait for uploads and may generate media;
turning publishing off does not make generation free.

Schedules are deployment configuration, not created by a Git merge. Set and
verify the scheduler timezone, target container, command and next execution.
The regular daily state protects posted dates and coordinates ownership. Eligible
pre-post failures may be retried before the deadline; a terminal rescue invocation
uses `PIPELINE_TERMINAL_RESCUE_RUN=true` and `PIPELINE_MODE=automatic`.

The isolated validation service has its own WHOOP readiness window, final check,
shared execution lock and one-generation-attempt-per-date guard. Those controls
and example schedules are documented in the Flow guide; they do not replace the
regular pipeline's publishing lifecycle.

## Failure handling and recovery

- Prompt requests have bounded retries and optional Gemini fallback. Invalid
  prompts do not proceed to media generation.
- Flow API fallback requires a known eligible failure. Forwarded or uncertain
  submissions, safety rejection, quota/account restriction, and model mismatch
  do not silently switch providers. Recover recorded media instead of regenerating
  to fix a download failure.
- `EMERGENCY_FALLBACK_ENABLED=true` permits the existing private `error_404_v1`
  fallback package for eligible terminal failures. Its manifest and prehosted
  media must validate; missing fallback assets produce an alert rather than a
  fabricated successful post.
- A successful Instagram publication remains posted even if later archive,
  portfolio or notification work fails. Repair secondary work using the recorded
  post identity; never post again merely to repair metadata or an archive.
- Preserve failed reports, claims and submission evidence. Do not delete state
  to force another generation. Logs and diagnostics are private runtime files;
  ordinary public documentation must not contain copied run records.

Back up the database consistently, including active SQLite journal/WAL state,
and preserve media referenced by the archive and public feed. There is no general
rule that all hosted files are safe to delete after 24 hours.

## Portfolio feed

The [portfolio contract](PORTFOLIO_METADATA.md) defines the six-field schema and
atomic dated/latest metadata. Publication requires a confirmed Instagram permalink
and both matching, publicly reachable videos. The videos use immutable dated
revision URLs; older runs cannot replace newer metadata. Normal and successful
emergency posts use their actual title and permalink. Secondary failures retain
the prior latest feed and cannot invalidate or retry the successful Instagram post.

The portfolio consumes one authoritative feed. Source selection and server
migration are operational decisions; private validation outputs are not a competing
public publisher.
