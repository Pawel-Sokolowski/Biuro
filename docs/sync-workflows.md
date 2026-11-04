# Mail and calendar workflows

This change builds on `677bab847d459a7630e79f9cdfec0d3c9a12e00d`.

## Calendar commits and retries

A local event save now writes its remote operation to `calendar_outbox` in the
same PostgreSQL transaction. A local deletion writes a deletion operation before
removing the event. The operation survives that deletion and application restarts.
Its payload remains JSONB; replacing a shared JSON settings document was necessary
for atomic concurrent updates, rather than for serialization speed.

Each operation is keyed by employee, provider, calendar and remote ID. PostgreSQL
assigns monotonically increasing revisions, including when a previously
acknowledged operation is enqueued again. A response acknowledges only the
revision that was actually sent. A newer local edit or deletion remains pending.
Pulls also lock the local event and reject remote changes while it has pending
local operations.

`calendar_sync_keys` persists a remote creation ID before making a network
request. Google receives an explicit UUID-based event ID; a retry first updates
that ID and reconciles a conflicting creation. CalDAV looks up its persisted UID
before creation. A timeout or authorization error during lookup propagates instead
of being treated as a missing event.

Provider operations serialize per employee/calendar using PostgreSQL session
advisory locks, including across application processes. Event/outbox row locks
are released before network calls, so users can continue editing locally. Calendar
links are checked before a late response is attached to an event. Calendar IDs are
included in uniqueness constraints and remote lookups. CalDAV operations retain
the configuration scope captured for their connection; unavailable selected
calendars produce an error instead of silently selecting another calendar.

The previous `google_pending_changes_<employee>` JSON setting migrates on first
Google synchronization, under a database row lock. Existing transactional
operations take precedence. Invalid JSON remains available for repair and raises
an error. Pending operations for a calendar that is no longer configured or
permitted remain stored; restoring its configuration/access permits retrying.
This does not provide conflict resolution for independent edits made in remote
calendar clients.

## Mail identity and workers

Reply/forward body lookup now requires the selected database row ID. Reused IMAP
UIDs therefore cannot redirect composition to a replacement message.

IMAP session and synchronization-lock caches include the database and schema.
Before reusing a session, the application reloads the account's connection
configuration. Connection/credential updates increment `connection_revision`.
Changes to the IMAP endpoint, port, transport or login also retire UID mappings,
retain cached bodies/history, and clear mailbox-generation checkpoints. Password
rotation reconnects without retiring the message cache. Header, flag, reference
and downloaded-body writes validate the captured connection revision alongside
UIDVALIDITY, including when two servers happen to advertise the same UIDVALIDITY.
Account locks precede folder/message locks consistently.

Mail reads use a latest-request runner. Superseded queued reads are removed;
already-running reads can finish, but their stale callbacks are rejected.
Commands use a separate Qt thread pool, with four worker threads and FIFO order
within each runner. Each command runner accepts at most 64 running/queued commands
and reports excess submissions. Refreshes never cancel commands. Closing a page
suppresses callbacks to its UI while accepted commands finish. Both kinds of
worker capture the submitting schema/context. Per-account parallel polling also
copies that context into its executor threads.

SMTP sends capture the accepted draft before submission and run on the command
queue. A failed/uncertain send reopens the draft for a user decision; it does not
retry automatically. This queue is in-process, whereas the calendar outbox is
persisted in PostgreSQL.

## Rendering and Excel

Mail uses Qt `QAbstractItemModel`/`QTreeView` with database row IDs as identities.
A refresh reconciles additions, removals, reordering and thread grouping, retaining
existing row objects. Unchanged retained results skip row preparation entirely.
Read-state changes invalidate that shortcut so thread summaries are refreshed.

Excel imports parse read-only worksheet rows as generators and retain the existing
500-row database write batches. An error rolls back the complete file import and
closes the worksheet source. Compatibility functions still return lists for callers
that explicitly need a materialized preview.

Report export captures controls on the GUI thread, then queries and writes in a
background command. The existing XlsxWriter dependency provides constant-memory
output. A completed temporary workbook replaces the destination atomically;
failures preserve the previous file. Text formula/URL interpretation is disabled,
and the existing cell sanitizer is applied. Report query results and aggregate
maps still consume memory proportional to their result size.

## Libraries and validation

These changes reuse Qt's model/view and worker APIs, XlsxWriter, caldav,
icalendar and psycopg. Hypothesis is a test-only addition, with its own hash-locked
dependency file. It generates mail-tree refresh/regroup/select sequences (up to
30 examples with 40 steps), checked against Qt's model tester. Deterministic tests
exercise actual PostgreSQL transactions and pause mocked network responses at
edit, delete, timeout and acknowledgment boundaries. Live provider credentials
are not required, and external-service behavior is simulated in these tests.

Run tests after installing both dependency locks as described in the README.
For the rendering microbenchmark:

```sh
QT_QPA_PLATFORM=offscreen python -m scripts.measure_mail_model origin/main
```

The script executes both versions' actual page-rendering methods with fresh
copies of identical query results, excluding database I/O and painting. On the
local Linux/Python 3.11/Qt 6.11 runtime, ten unchanged-refresh samples produced:

| Retained rows | Baseline median | Current median | Retained row objects |
| --- | ---: | ---: | ---: |
| 500 | 18.663 ms | 0.060 ms | 500 |
| 5,000 | 203.120 ms | 0.643 ms | 5,000 |

Both current cases emitted zero model resets, insertions or removals. These
numbers describe the unchanged-result shortcut, not initial load, changed-page
rendering, database latency or end-to-end frame rate.

## Database upgrade

Run `python migrate_db.py` before starting the updated application. Alembic
revision `20261104_02` adds the outbox, identity bindings, scoped indexes and IMAP
connection revisions. Re-running the upgrade is supported. The historical
bootstrap revision remains frozen. Downgrading this revision is deliberately
unsupported because dropping an outbox can discard unsent changes; use a verified
backup when reverting the database.
