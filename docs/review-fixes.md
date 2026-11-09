# Repository review fixes — PR 62

All 27 findings in the November 8 repository review are addressed. Financial writes lock and revalidate invoice state; mail rename never uses expunging CLOSE; manual rules carry stable row IDs; actor-aware chat reads and serialized sends enforce access and ordering. Registration requires an authorized administrator. Private conversation duplicates merge before uniqueness is enforced.

Imports distinguish absent columns, conflicting identifiers and repeated invoice lines. Versioned source hashes support safe transactional adoption of matching legacy rows. Reports consistently honor company rate overrides and convert seconds to hours. Timer sessions retain their original month, including recovery; timer edits lock before computing counter adjustments.

CalDAV preserves complete resources and detached occurrence identities, with conditional writes when ETags are supplied. Google/CalDAV conversions use Warsaw timezone explicitly. Paper UPL validation is independent of the older XML schema. Forwarded attachments retain separate payloads and stale body reads cannot repopulate cleared contexts.

Seller details are frozen at issuance and reused for preview and generated KSeF XML. Accepted legacy documents use verified locally archived XML where available; absent historical details are not reconstructed from current settings. PDF saves validate staged output before atomic replacement.

Monthly/year-end reads run asynchronously, repeated reads of the same context coalesce, year-end filtering uses cached data, and company backup metadata has a bounded cache independent of search. No new runtime dependency or global JSON replacement was introduced.

## Deployment

Run `migrate_db.py` or `Biuro.exe --migrate` before starting updated desktops. New revisions `20261108_01` and `20261108_02` merge duplicate private conversations, enforce private-channel uniqueness and add seller snapshots. Rebuild the executable/installer and update desktops together. Historical imports already collapsed under old hashes cannot be reconstructed without their original source files; the upgrade does not guess or delete historical revenue.

## Validation

The complete local run covered 126 test modules. Two invoice fixture modules initially failed because they relied on mutable seller settings; both passed after explicit historical seller snapshots were added (38 tests rerun). All other modules passed. Ruff, focused mypy and dependency compatibility pass. Migration repeatability and the source release smoke are checked on a disposable schema. Windows packaging and actual desktop notification/Word COM behavior require the PR's Windows CI and desktop validation.
