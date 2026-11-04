# Optimization review: implementation decisions

Base: `ffef491` (4 November 2026). Fix branch: `fix/review-optimizations`.

| Review item | Implementation / decision |
|---|---|
| IMAP UIDVALIDITY | Persist the folder generation. On a change (or first adoption of unknown legacy UIDs), retire old UID mappings without deleting their bodies or assignment history. Fetch new headers and give reused UIDs new row identities. The viewer rejects stale body callbacks and clears a retired message preview. |
| Missed flag changes | Reconcile flags every ten polling runs and on process startup, even when aggregate STATUS counters are unchanged. Retain the daily recovery marker. |
| Header database polling | Remove the duplicated lookup and cache company/task labels for the lifetime of each active timer. Elapsed display updates remain local. |
| Mail list/search | Debounce typing, fetch at most 500 rows per page, search recipient/subject/sender before limiting, and page using `(date, id)` rather than unstable offsets. GUI actions use bounded background jobs and reject stale results. |
| UI background work | Shared Qt worker abstraction for report generation, imports, mail lists and periodic dashboard reads. Capture schema context per job. Prevent overlapping imports; cancellation of read results never cancels a committed write. Small compatibility APIs used by scripts/tests remain synchronous. |
| Connection pooling | psycopg_pool bounds total checked-out connections (eight per DSN/schema pool), handles health/lifetime, and retains the existing tuple/name row API and rollback-on-return behavior. Row name lookup uses a precomputed index. |
| Year-end round trips | Read missing snapshots and filing records in batches; reuse a four-query warm read, index owners/filings in memory, and ensure only the edited company's snapshot when saving a declaration. Preserve historical filing/snapshot rules. |
| Migrations | Alembic baseline preserves the existing accounting bootstrap in `schema_baseline.py`. Upgrade commands serialize concurrent migration attempts and record the revision; repeat upgrades do not replay historical DDL. `init_db` remains an explicit bootstrap/repair compatibility API. Future DDL goes in new revisions. |
| XML signatures | lxml builds and canonicalizes actual XML nodes with Exclusive C14N. Independent tests verify RSA and EC signatures, SignedProperties and document digests, including escaping and carriage returns. No additional signing framework was required. |
| Imports | Read optimized workbook rows directly into the report parser, close workbooks, and batch parameter writes (500 at a time) using psycopg executemany/pipeline. Return DB connections before registry requests. The preview wizard retains its list API; this is not a promise of constant memory for every import. |
| Monetary arithmetic | Use Decimal/HALF_UP explicitly in XML quantizers and report pricing calculations. Retain existing invoice JSON/UI float interfaces and historical document amounts; an end-to-end serialization/data-type migration is intentionally separate. |
| KSeF package memory | Incrementally decrypt/hash HTTP chunks into spooled temporary storage; verify declared sizes/hashes, aggregate ZIP expansion and file-count limits. Preserve the existing dictionary return API and submission retry protections. |
| Startup | Defer six main pages until accessed. Keep dashboard, monthly timers, mail and chat available for current background monitoring. Permissions are applied before a deferred page is selected. |
| Old reports | Remove unreachable UI renderers and imports. Retain public legacy calculation helpers for compatibility; they are not called by the current report UI. |
| Dependencies/repository | Remove django-qrcode, declare directly imported calendar packages, add pool/migration/IMAP codec dependencies, lock the cross-platform Python 3.11 graph with hashes, use the lock in CI, and remove 58 tracked bytecode artifacts. |
| Date helpers | Use calendar.monthrange for all three last-day calculations. |

## Library choices checked and retained

- **IMAPClient** replaces the handwritten modified UTF-7 codec. A wholesale replacement of LIST/FETCH/session behavior is not needed for the verified fixes and would change a wider protocol surface; existing folder and synchronization tests remain relevant.
- **Zeep** was not added. Adopting a different GUS WSDL/session/MTOM stack requires live integration validation, beyond the demonstrated performance fixes. No generic SOAP rewrite is included.
- **python-stdnum / email-validator** were not added. Checksum enforcement and expanded address acceptance would change accepted input, legacy import behavior and fixtures. The review suggestions were optional validation-policy changes, not demonstrated replacement bugs.
- **Pydantic / pandas / Polars / qasync / APScheduler** were not needed. Existing Qt, Decimal, openpyxl and psycopg primitives handle these fixes with a smaller dependency footprint.
- **SignXML/xmlsec** were not needed once lxml canonicalization removed the handwritten canonical-byte assumption. Live KSeF test/demo certification remains distinct from independent local signature verification.
- Keep **openpyxl and XlsxWriter**: the latter still implements a specific Saldeo export. Consolidation would need export-format validation and provides less value than fixing the active I/O paths.
- Keep WebEngine's restricted profile and Qt's PDF renderer. Changing the HTML engine changes mail rendering; it is not a transparent package-size optimization.
- Keep financial authorization, audit records, accepted-XML PDFs, uncertain KSeF/SMTP state handling and manual payment confirmation.

## Deployment and limits

Install with `python -m pip install --require-hashes -r requirements.lock` and run
`python migrate_db.py` before login. On first IMAP generation adoption headers
may be downloaded again; old cached bodies and assignment history are retained.
Future migrations must update the expected schema version in `app/db/schema.py`
and write the matching marker in the new revision. Do not edit the frozen baseline.

The native Windows installer and real IMAP/GUS/KSeF/SMTP servers need release
smoke testing. Unit/integration tests use fake external transports and temporary
PostgreSQL schemas. A dependency lock is not a vulnerability audit or a Windows
installer certification.

## Validation

- Full locked-runtime run: **683 tests in 88 modules passed**, without skips.
- After the final row-identity guards, all **21 affected mail/KSeF/regression modules passed** again.
- New coverage includes twelve regression tests and independent RSA/EC signature cases.
- Ruff checks for syntax/undefined names (`E9,F63,F7,F82`) passed.
- Installed-package compatibility and `git diff --check` passed.
- Python 3.11, PyQt6 6.11.0 and the committed hash-locked runtime were used.
