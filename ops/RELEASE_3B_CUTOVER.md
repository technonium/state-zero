# Release 3b: completed-date cutover and rollback

This is a review procedure, not deployment authorization. No historical state is migrated.

## Record contract

New controlling parent records (Flow, or API-default diagnostics) carry `record_version: 2`. An existing parent with an absent, invalid or unfamiliar version fails closed before submission, API generation or record replacement. Existing provider-operation and child marker/context/receipt contracts stay unchanged; Flow fallback keeps its API reservation in the Flow parent. Completed historical records remain readable by offline audit tools and are never upgraded in place. The normal pipeline's existing POSTED guard remains authoritative.

| Removed parent field or inference | Remaining authority |
| --- | --- |
| `generation_started`, `daily_policy` | Conservative numbered reservation and explicit record version |
| `attempt.submit_attempted`, copied failure `submission_state` | Existing child forwarding/block evidence; a missing marker never proves no send |
| Mixed `attempt.status`, `attempt.recovery_exhausted` | Separate typed attempt failure; persisted search/retrieval limits and terminal provider failure |
| Parent `result.succeeded` | Existing exact child receipt and validated original bytes; upstream and child contracts unchanged |
| Duplicate `output_sha256` | One `selected_sha256`/`selected_file`/provider, committed before promotion; distinct original checksum retained |
| Completion labels in `api_fallback_state` and `logical_attempt` | Immutable `api_reserved` or existing logical reservation, resumable operation, selected provider and processed bytes |
| `retry_count`, `submissions_estimated`, duplicate success notification branch | Durable attempts/worker counters and the existing deduplicated completion transition |

A pre-submit exception hint without child evidence now consumes one uncertain reservation. It cannot authorize another CLI submission or an early paid API call; unresolved-at-noon handling remains. A confirmed failed generation still permits the existing bounded replacement. Restrictions and identity/cleanup failures still block it.

## Before an approved rollout

1. Use a fully completed date: confirmed POSTED, archive/database/public/portfolio/ingestion agreement, no uncertain generation or publication. The October 26 run satisfies this prerequisite; if deployment is later, reassess the most recent completed date.
2. Verify no canonical execution owner or surviving browser. Respect the 10:00–14:30 IST deployment exclusion. Do not deploy over an active date with legacy records.
3. Retain the exact running 3a image and verify its off-host archive. Record read-only state/database/media fingerprints and the current source/configuration. Do not restore or edit historical files.
4. Deploy only after the owner's diff review and explicit deployment approval. The first new active date must create version-2 parent records. If an existing version is encountered, stop and reconcile rather than deleting it to obtain a clean start.
5. Verify installed source/tests, unchanged dependency/model/prompt/schedule settings and original resources, plus unchanged completed-date fingerprints. Observe the next real scheduled run; do not manufacture a paid probe.

## Rollback

- Before any new-format active date: after the owner check, the retained exact 3a image may be restored under separate rollout approval. Leave state/database/receipts untouched.
- Once any version-2 date is active: **stop automatic execution and reconcile first**. Pausing timers or stopping a run is an operational action requiring explicit human authorization; this change performs neither.
- Preserve parent records, child context/markers/receipts, cumulative allowances/deadlines, API reservation/operation, selected/original bytes and publication intent/receipt. Establish exact generation/upload/publication facts; never infer authority from fields old code does not understand.
- Resume old code only at a safe completed-date boundary, with no active or uncertain date it can interpret. An in-memory compatibility adapter would require separate review; none is included.
- Never replace current state with an older snapshot, erase a reservation, reset a claim, replay publication or delete evidence to make old code start.

## Replay proof required for review

The suite covers missing/corrupt/unfamiliar records, reservation/child/receipt crashes, missing child completion proof, processed/API promotion crashes, failed-clip replacement, malformed policy reasons, retained worker reply, expired/exhausted retrieval, cleanup ownership, and selected-result precedence. Upload recovery, Telegram manual behavior, publication protection and claim authority retain their existing tests and implementation.
