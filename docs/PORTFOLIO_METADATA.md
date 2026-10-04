# Portfolio metadata feed

After a confirmed Instagram post and successful portfolio upload, State Zero
publishes `/portfolio/YYYY-MM-DD/metadata.json` and `/portfolio/latest/metadata.json`
under `VPS_PUBLIC_BASE_URL`. This includes successful emergency fallback posts.

Schema version 1 contains only `schemaVersion`, `date`, `title`, `instagramUrl`,
`lightVideoUrl`, and `darkVideoUrl`. Video URLs always point to the dated archive.
Consumers should fetch latest metadata on their regular refresh, then use its URLs
together. No additional Instagram lookup is needed.

Both dated videos must be publicly reachable before metadata is published. Files
are replaced atomically, dated first and latest last. Failed metadata delivery
retains the previous latest feed and cannot retry or invalidate an Instagram post.
Posting-disabled runs publish no feed. No new environment variables are needed.
The first qualifying post after deployment creates the feed; there is no backfill.
