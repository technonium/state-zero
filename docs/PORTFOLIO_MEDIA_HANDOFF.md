# State Zero → Portfolio Media Handoff

State Zero can publish optional light/dark portfolio sidecars alongside its
normal Instagram card. The feature is disabled by default and uses the
existing VPS configuration when enabled.

## Enablement

Set this only in the private runtime environment:

```env
PORTFOLIO_MEDIA_ENABLED=true
```

Existing installations remain unchanged when the variable is unset or false.

## Generated media

The daily renderer builds both themes from the original artwork and source
video, rather than cropping the completed Instagram card. It preserves the
date, metric arcs, spark mark, title, and artwork while omitting the Instagram
footer treatment.

The current delivery dimensions are:

```text
still: 1080×1701 WebP
video: 720×1134 MP4
```

The MP4 uses H.264, `yuv420p`, `+faststart`, and retains source audio when it
exists. It is capped at 1.5 MB.

## Private and public paths

Canonical per-run files remain under the configured private root:

```text
$STATE_ZERO_PRIVATE_ROOT/runtime/output/YYYY-MM-DD/portfolio/
  light.webp
  dark.webp
  light.mp4
  dark.mp4
```

When live VPS upload is enabled, the same files are copied to both the dated
archive and the stable aliases:

```text
<VPS_SSH_PATH>/portfolio/YYYY-MM-DD/{light,dark}.{webp,mp4}
<VPS_SSH_PATH>/portfolio/latest/{light,dark}.{webp,mp4}
```

The public consumer URLs are documented in
[`PORTFOLIO_MEDIA_CONSUMER_HANDOFF.md`](PORTFOLIO_MEDIA_CONSUMER_HANDOFF.md).

## Pipeline behavior

Portfolio rendering and upload run only after the primary Instagram flow has
completed. A portfolio failure is reported as a warning and does not turn a
successful Instagram post into a failed pipeline run.

Emergency fallback sidecars are optional. If prebuilt sidecars exist under
`$STATE_ZERO_PRIVATE_ROOT/runtime/fallback/error_404_v1/portfolio/`, a
successful emergency fallback post stages and publishes them through the same
dated/latest paths.

Relevant implementation files:

```text
src/scripts/portfolio_media.py
src/scripts/pipeline.py
src/scripts/emergency_fallback_manager.py
tests/test_portfolio_media.py
```

Keep credentials, WHOOP data, runtime state, generated daily media, and local
deployment paths outside this public repository.
