# Portfolio media and metadata

Set `PORTFOLIO_MEDIA_ENABLED=true` in the private runtime environment to render
light and dark portfolio variants. The feature is disabled by default.

## Media files

Each run renders from the source artwork and video, keeping the date, metric
arcs, title, and artwork without the Instagram footer. Files are stored under
`$STATE_ZERO_PRIVATE_ROOT/runtime/output/YYYY-MM-DD/portfolio/`:

| Files | Format |
| --- | --- |
| `light.webp`, `dark.webp` | 1080×1701 stills |
| `light.mp4`, `dark.mp4` | 720×1134 H.264 videos, at most 1,500,000 bytes each |

Videos retain source audio when present and use `yuv420p`, fast-start playback,
BT.709 primaries/matrix, sRGB transfer, and limited range.

Posting-enabled runs upload a staged, immutable pair to
`/portfolio/YYYY-MM-DD/<revision>/`, where revision is a SHA-256 fingerprint of
both video files. Existing revision files are never overwritten. Compatibility
`latest` aliases remain available, but the feed points at the immutable pair
using the existing VPS configuration. Posting-disabled runs keep the renders
private and publish no metadata. Portfolio rendering, delivery, and notification
failures cannot retry or invalidate a successful Instagram post.

## Public feed

After confirmed Instagram publication and successful portfolio upload, the
pipeline publishes these paths under the configured `VPS_PUBLIC_BASE_URL`:

```text
/portfolio/YYYY-MM-DD/metadata.json
/portfolio/latest/metadata.json
```

Schema version 1 contains exactly six fields:

```json
{
  "schemaVersion": 1,
  "date": "YYYY-MM-DD",
  "title": "Actual artwork title",
  "instagramUrl": "https://www.instagram.com/p/POST_ID/",
  "lightVideoUrl": "https://media.example.com/portfolio/YYYY-MM-DD/REVISION/light.mp4",
  "darkVideoUrl": "https://media.example.com/portfolio/YYYY-MM-DD/REVISION/dark.mp4"
}
```

The example host is a placeholder; exports use `VPS_PUBLIC_BASE_URL`, including
any configured path prefix. The title and permalink come from the same published
run. No additional Instagram lookup or credentials are needed for consumers.

Fetch `latest/metadata.json` on refresh, then use its dated video URLs together
with its Instagram permalink. Matching WebP posters are available beside those
videos. Avoid independently combining `latest` media aliases and an Instagram
link: the aliases may change between requests.

A valid published permalink and both publicly reachable dated videos are
required before metadata is written. The six public fields and schema version
remain unchanged. A destination filesystem lock serializes comparison and
replacement, dated first and latest last. The persisted daily claim's start
time identifies the generation attempt; upload/completion time is never used
as its ordering key. Later dates win, then later generation starts within a
date. Equal ordering keys with conflicting media are rejected; retries of the
same revision finish idempotently. Older runs can archive their own date but
cannot replace newer dated/latest metadata.

Ordering guards live in `.portfolio-publication/` with private permissions
(directory 0700, JSON 0600) and contain only date, attempt time and revision.
They are separate from public feed JSON. The SSH publisher executes the same
stdlib-only Python 3 code on the destination and uses a kernel file lock, which
is released automatically when the process exits. Keep that private directory
on persistent storage and excluded from static serving.

Missing inputs or failed delivery leave the previous latest feed
intact. The first qualifying post creates the feed; existing dates are not
backfilled automatically. Only the fields above are public; private inputs and
health inputs remain outside the repository and public feed.

The portfolio consumes one authoritative public feed. Configure its source URL
to match the active publisher; private non-posting runs do not publish metadata.

## Emergency posts

If prebuilt portfolio sidecars exist under
`$STATE_ZERO_PRIVATE_ROOT/runtime/fallback/error_404_v1/portfolio/`, a successful
emergency post copies them into its dated portfolio directory and publishes them
through the same path. Metadata uses the fallback manifest's actual title and
the successful post's permalink. Missing sidecars do not prevent the emergency
Instagram post.

## Implementation

- [Portfolio renderer](../src/scripts/portfolio_media.py)
- [Metadata validation and atomic delivery](../src/scripts/portfolio_metadata.py)
- [Pipeline integration](../src/scripts/pipeline.py)
- [Emergency fallback manager](../src/scripts/emergency_fallback_manager.py)
