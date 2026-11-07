# Reliability and performance changes (1.5.0)

Run `python migrate_db.py` before starting the updated application, or use
`Biuro.exe --migrate`. All desktops sharing the database should use this release.
The new Alembic revision is `20261106_01`; the expected schema version is
`2026-11-06.1`. The migration is transactional and does not replay the frozen
bootstrap on an already versioned database. Allow maintenance time for the
invoice column conversion and mailbox index creation on large databases.

## Shared data correctness

- Native IMAP MOVE understands byte capabilities. The fallback only expunges
  the selected UIDs when UIDPLUS is available. Older servers keep the Deleted
  flag until their normal cleanup; the application never runs folder-wide
  EXPUNGE as part of an individual move/delete. A failed COPY/STORE sequence
  reports its partial result instead of pretending it succeeded.
- Draft editors and regeneration carry an invoice revision. A stale save raises
  a conflict and preserves the interactive editor's contents. Row locking still
  protects against simultaneous issuance.
- Company and employee report identities use database IDs. Imported ambiguous
  employee names/initials require an ID or unique email rather than choosing
  the first employee.
- Revenue allocation distributes whole grosz deterministically, including
  negative corrections. Invoice header totals and imported invoice amounts use
  `NUMERIC(18,2)`; migration rounds existing binary-float values to two places.
  Existing public invoice DTOs retain float values for compatibility; formatting
  and allocation convert through Decimal. Pricing/other legacy columns are a
  separate migration, not silently converted in this release.
- Legacy profitability helpers now delegate to the canonical invoice/time
  dataset instead of assuming a 50/50 accounting/payroll split. Expected-price
  reports remain distinct from actual billed revenue. Daily document averages
  use grouped prefix sums and exclude zero months.

## Responsiveness and coordinated work

Invoice/payment/settlement reads, debounced client searches, unread badges and
chat polling run in bounded background pools. Results are applied only to the
request/conversation they belong to. The invoice list and profitability detail
use a shared Qt model; invoice preview actions are painted by a delegate instead
of allocating a button per row. Refreshes preserve selected record IDs and the
top visible record. This is not database paging: very large annual lists may
still warrant a paged query/model.

Monthly generation preloads companies and shared accounting/payroll pricing.
Settings, permissions, import column metadata, tax offices and employee lookups
are cached only within an operation. A new job sees changes from other desktops.
Per-invoice transactions and validation remain in place.

PostgreSQL advisory try-locks coordinate IMAP work between desktops. Automatic
header checks share a 90-second cooldown; manual requests bypass the cooldown
but respect an active lock. A database disconnect releases leadership. These
locks do not replace UIDVALIDITY checks. They use a separate coordinator pool, capped at four connections per database/schema,
so account checks cannot occupy the eight data-pool slots. This caps a process at
12 pooled connections rather than eight; both pools expire idle connections. The
cooldown read transaction ends before network I/O. Explicit synchronization
reports Busy instead of claiming a skipped download completed.

Mailbox substring searches retain literal `%`/`_` semantics. Optional partial
GIN trigram indexes match the active COALESCE expressions. If the application
role cannot install `pg_trgm`, migration continues without these indexes; a DBA
can enable the extension and run the explicit schema repair in a maintenance
window. The existing keyset paging index is retained.

A local synthetic fixture of 50,000 messages measured the real paged substring
query with `EXPLAIN (ANALYZE, BUFFERS)`: 0.486 ms with maintained trigram indexes
versus 70.931 ms without them. Immediately after bulk insertion, GIN's pending
list made both plans use a scan (~60 ms). Normal GIN/autovacuum maintenance
matters. These are fixture measurements, not production/NAS latency guarantees.

## Imports and documents

Company CSV/XLSX rows are streamed; even early exit closes readers. Fast company
import first presents a read-only summary of creates, updates, invalid rows,
conflicts and unsupported columns. It blocks an invalid/ambiguous plan. The
summary does not lock companies until import: final counts can change if another
employee changes the database meanwhile. Currency-account columns remain
explicitly unsupported rather than being silently discarded. Long bank-account
cells stored as Excel numbers are rejected: converting them to text afterward
cannot restore already lost digits. Identifier checksum warnings use
`python-stdnum` and allow review of legacy identifiers.

The all-data wizard and fast company import run accepted writes in a worker
with an application-modal progress dialog. Closing the progress dialog cannot
cancel a half-committed import. Other import dialogs keep their existing
transaction/cancellation semantics. Binary XLS is no longer offered by readers
that support only CSV/XLSX.

Seven editable Word templates use named `docxtpl` placeholders. The preparation
script is a development tool; runtime filling does not guess table coordinates
for these fields. Existing checkbox policies and government PDF overlays remain
in place. Google OAuth login/refresh uses `google-auth` and
`google-auth-oauthlib`; encrypted refresh-token storage and calendar outboxes
remain unchanged. JSON, psycopg and the established generation guards remain.

## Timers, diagnostics and release checks

Active timers persist an owned session and checkpoint elapsed seconds every
15 seconds. Duration uses a monotonic clock, while wall time supplies report
dates. After a crash, **Odzyskaj stoper…** offers sessions with no heartbeat for
90 seconds. Recovery claims ownership atomically, excludes the closed interval,
and prevents the old process from completing the recovered session. Up to the
last checkpoint interval may be lost. A network outage can make an otherwise
live timer recoverable; the old owner then cannot write its final billing.

Installed logs rotate at 5 MiB with four backups in the application's data
`logs` directory. They include application/Python version, job durations, invoice
row counts and pool waits. Known DB passwords and the encryption key are
redacted from messages and exception text. External exception strings can still
contain operational details; inspect logs before sharing them publicly.

Hardware rendering is the default. Set `BIO_SOFTWARE_RENDERING=1` in the local
configuration for a driver that needs the software fallback; an explicit
`QT_OPENGL=software` is also respected.

`app/VERSION` supplies the application and installer version. Pinned, hash-locked
build tools include PyInstaller, Ruff and mypy. CI checks Python correctness,
focused financial/DTO types, the full test suite, and a Windows build. The Windows
job migrates twice, runs the packaged EXE smoke check, builds and silently
installs the installer, checks the installed EXE, and uninstalls it. The smoke
check validates bundled forms/schema/dependencies and generates sample Word and
vector PDF files without sending mail or contacting external services. WinRT
imports are checked; actual notification activation and Word COM conversion
still require a Windows desktop check.

The source smoke and isolated PostgreSQL tests can run on Linux. A successful
Linux run does not establish that the Windows release job passed. The new
Windows job must finish before treating a particular installer as validated.

## Follow-up boundaries

The shared request/worker/model/money/DTO helpers reduce duplication in active
paths. This release does not rewrite all of MailPage/CompanyForm, replace the
entire IMAP transport, convert every financial column, introduce an ORM, or
replace JSON. Those larger changes should follow measured need and separate
compatibility tests.


## Review follow-up optimizations

Timer completion now passes its existing database connection through additional
service recording and draft creation. Time, active-session removal and billing
commit or roll back together. A failed commit followed by retry cannot create a
second billable service. Annual fees allocate whole grosz across months before
employee allocation, preserving totals even with discounts and negative totals.

Chat acknowledgements capture the highest displayed message ID and recheck the
current tab visibility. A queued acknowledgement never marks later messages
read and is sent only when its watermark advances. Legacy watermark
initialization and unread counting share one SQL round trip. Mail badges and
notification candidates use one consistent snapshot and at most four data
queries across all configured accounts; candidates are bounded per account and
respect hidden folders and notification-excluded roles.

Bulk invoice generation runs through CommandRunner, reports per-company progress,
and guards duplicate clicks, including while its confirmation dialog is open.
Accepted writes finish even if their page is destroyed. Issued invoice and
profitability tables store TableRecord values rather than per-cell Qt objects;
the model emits insert/remove and changed-column signals by stable row keys.
Legacy item adapters are created on demand for existing action code. A local
15-column offscreen benchmark (median of four runs, fill plus first frame)
measured 200 rows at 15.79 ms before / 11.24 ms after, and 2,000 rows at 99.88 ms
before / 31.77 ms after. These numbers exclude database/network latency.

Import employee aliases and tax-office tokens are prepared once per operation.
Fast company import reuses its preview's native rows from a private temporary
spool, so it does not parse XLSX twice or repeat CSV encoding detection. The
source's identity, size and timestamps are checked before applying the approved
snapshot; a changed source must be reviewed again. Native dates and numeric
cells are preserved. The spool is closed on cancellation, failure or completion.

Packaging excludes third-party test/example modules from explicit runtime hidden
imports. CI type checks now include generic worker callback signatures, compact
table records, the import spool and mailbox notification result types.
