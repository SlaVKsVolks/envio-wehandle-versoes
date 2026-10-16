# Offline matching and dry-run review

A small Python 3.12+ helper that takes **already-extracted page text** and a
locally supplied roster snapshot, then produces advisory JSON/CSV results.
Only the Python standard library is required. Run directly from this folder;
no installation, service, browser, plugin or account setup is needed.

**There is no PDF reader, renderer, OCR, authentication, network adapter or
upload implementation.** This is a standalone optional helper, not a drop-in application patch. Nothing here
checks current portal state or authorizes sending documents.

## Quick synthetic example

From this folder, with an explicitly selected Python 3.12+ interpreter:

```text
python -I -B run_dry_run.py --help
python -I -B run_dry_run.py --pages examples/pages.json --roster examples/roster.json --account synthetic --company 1 --unit 2 --service 3 --document-type 100 --competence 2026-09 --output review-example
```

The output directory must not exist and its parent must already exist. The
example contains seven invented pages: three CPF candidates, one name-only
suggestion, two unresolved pages and one inactive-worker exclusion. Its fixed
snapshot is deliberately old; a stale/blocked review is expected, not a fault.
The fixtures were constructed for tests, not taken from employees or production.
Example scope/document identifiers are placeholders, not real account mappings.

Exit 0 means a local bundle was produced, **not approved or sent**. Exit 2 prints
a fixed error code and safety fields without raw input, identities, file paths
or a traceback. Standard output on success contains counts and a report hash.

## Input contracts

`pages.json` is a UTF-8 JSON object with exactly:

```json
{"schema_version": 1, "pages": ["first page text", "second page text"]}
```

Order is significant; one string represents one original page, including an
empty string for an unreadable page. Do not feed PDFs or binary files here.

The roster envelope contains exactly `schema_version` (integer 1), `scope`,
`observed_at`, `source_reference`, and `employees`. See the complete synthetic
example. `scope` has exactly `account_id`, `company_id`, `unit_id`, `service_id`.
Each employee has exactly `employee_id`, `name`, `cpf`, `active`, `scope`.
Each employee's scope must equal the envelope and explicitly selected scope.

- Account selector: 1–48 lowercase letters/digits/underscores, starting with a
  letter. It is a local label, not a login, email, password or token.
- Company/unit/service/employee/document IDs: positive decimal strings, up to
  64 digits, without leading zeroes. IDs are never guessed from names.
- CPF: 11 digits or standard punctuation, with valid check digits. Names are
  trimmed, non-control-character text up to 200 characters.
- `active` is a JSON boolean. Unknown fields, duplicate keys/employee IDs,
  malformed values and conflicting scope are rejected.
- `observed_at` is an explicit-timezone timestamp including seconds.
  Freshness is a declared 24-hour window, with five minutes of future skew.
  A snapshot's origin/time are not authenticated live evidence.
- `source_reference` is a non-secret 1–80-character ASCII label using letters,
  digits, underscore, dot, colon or hyphen. It is not a URL or credential.
- Monthly documents use `--competence YYYY-MM`; use `--nonmonthly` instead
  for nonmonthly documents. Optional `--employer-cnpj` enables comparison
  only against explicitly labeled employer-CNPJ text; absent hints are not
  called verified. No vendor-specific parsing is provided.

## Conservative matching

A unique, valid CPF associated with one active in-scope employee produces a
candidate. A unique full-name occurrence is only a suggestion. Invalid or
unknown CPF evidence cannot fall back to a name. Conflicting identities and
duplicates remain ambiguous; inactive employees are excluded. A page without
identity is never attached to the previous employee. Consecutive independently
identified pages form groups; separate occurrences are flagged for review.
Month/employer conflicts and stale snapshots block a clean review.

## Results and safety

- `report.json`: every page, opaque employee IDs, grouping, reasons, declared
  scope/month/snapshot context, aggregate counts and input SHA-256 hashes.
- `report.csv`: semicolon-delimited UTF-8 with BOM, one row per page, advisory
  status and explicit safety fields. Import numeric identifier columns as text
  in spreadsheet software to avoid automatic conversion of long identifiers.
- `COMPLETE.json`: hashes of the JSON and CSV. A bundle is complete only when
  this exists **and `INCOMPLETE` is absent**. Failures may leave an incomplete
  directory; do not reuse it or treat its files as completed work.

All results have `mode=dry_run`, `upload_enabled=false`,
`execution_status=blocked`, `reason_code=UPLOADS_NOT_IMPLEMENTED`,
`network_requested=false`, `remote_state=not_checked`, and
`authorization_status=not_granted`. These values are not configurable flags.
Candidates and suggestions always require human review.

Reports omit CPF, employee names and page text. They still contain declared
scope and employee IDs, which can be sensitive. Keep inputs and results in
private, ordinary, non-synced local folders. No folder sharing, encryption or
data-retention policy is configured for you. No real inputs/results belong in
this contribution folder or a PR.

The helper reads originals without writing them, rechecks identity/content
before completion, refuses existing output targets, and rejects network/device
paths and observed symlink/junction/hardlink inputs. It is **not an OS sandbox
or an adversarial concurrent-filesystem guarantee**. Do not expose it as an
untrusted public service. It does not stop other software from syncing a folder.
Completion hashes bind generation-time content, not later edits; reverify before
reuse. A text extractor and any remote integration remain separate systems.

Bounds: 10 MiB per input JSON file, 20,000 roster entries, 200 pages, 100,000
characters per page, 20,000 cumulative page-to-employee associations and 32 MiB
per report artifact. Bounds reject rather than truncate. There is no worker
process, hard CPU-time/memory enforcement or native-parser security claim.

## Library and tests

Library entry: `document_matching.review.build_review(pages, roster_raw,
scope, document, now)`. `pages` is a list of strings, `roster_raw` is JSON bytes,
`scope`/`document` use the models module, and `now` is timezone-aware. The CLI
adds file handling, original-input rechecks and bundle completion hashes.

```text
python -B -m unittest discover -s tests -v
```

Tests use only synthetic fixtures and owned temporary directories. An optional
developer environment can also run pytest and Ruff; neither is a runtime
dependency. The intended upstream contribution is conservative matching tests
and an advisory helper, not a replacement authentication/upload stack.

See `CONTRIBUTION.md` for the scope and integration boundary.

`MANIFEST.json` lists the exact source, documentation and synthetic-fixture
files included in this contribution with their sizes and SHA-256 hashes. The manifest
does not hash itself. Development tools, diagnostics and generated reviews are
not part of the allowlisted archive. Hashes establish integrity, not permission
to publish or proof of production acceptance.

The scoped `.gitattributes` preserves LF endings for these text files so their
manifest byte hashes remain stable after Git checkout, including on Windows.
