# Portfolio media consumer handoff

The State Zero pipeline publishes a lightweight portfolio set after a successful daily Instagram run. The portfolio should use the stable `latest` URLs and does not need a manifest, database query, or access to the private runtime.

## URLs for the portfolio

Set the portfolio media base URL to the same host configured as `VPS_PUBLIC_BASE_URL`, then request:

```text
{VPS_PUBLIC_BASE_URL}/portfolio/latest/light.webp
{VPS_PUBLIC_BASE_URL}/portfolio/latest/dark.webp
{VPS_PUBLIC_BASE_URL}/portfolio/latest/light.mp4
{VPS_PUBLIC_BASE_URL}/portfolio/latest/dark.mp4
```

The pipeline replaces all four `latest` files after each completed daily run. Use the WebP as the still/poster and the matching MP4 for playback.

For an immutable historical run, replace `latest` with its ISO date:

```text
{VPS_PUBLIC_BASE_URL}/portfolio/YYYY-MM-DD/{light,dark}.{webp,mp4}
```

## Pipeline storage contract

The canonical generated files stay private first:

```text
$STATE_ZERO_PRIVATE_ROOT/runtime/output/YYYY-MM-DD/portfolio/
  light.webp
  dark.webp
  light.mp4
  dark.mp4
```

They are then copied to the public VPS archive and stable `latest` paths. The portfolio consumes only the public paths above.

Portfolio media is enabled by this deployment environment variable:

```env
PORTFOLIO_MEDIA_ENABLED=true
```

Emergency fallback sidecars are installed privately at:

```text
$STATE_ZERO_PRIVATE_ROOT/runtime/fallback/error_404_v1/portfolio/
```

If an emergency fallback post succeeds, the pipeline stages those sidecars into that run's private date directory and publishes them through the same archive and `latest` URL convention.
