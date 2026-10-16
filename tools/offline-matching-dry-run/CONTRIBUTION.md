# Standalone contribution scope

This optional helper is proposed alongside the repository's packaged releases.
It is not integrated into the installed application and does not replace or
modify release assets, the updater, authentication, PDF extraction or sending.

It supplies conservative page-to-employee matching, local JSON/CSV advisory
reports, synthetic regression tests and examples. It accepts already-extracted
page text plus one explicitly scoped roster snapshot and requires Python 3.12+.
No remote operation, uploader, PDF reader, renderer or OCR is implemented.

An application may provide its own extracted text and explicitly authorized
roster snapshot through the library entry documented in README.md. Such an
adapter is outside this contribution: scope/domain mapping, runtime compatibility
and UI selection require a separate review against the application's source.
An exact match remains a candidate, not authorization to send a document.

The examples are synthetic. Do not commit real documents, generated reviews,
employee/account identifiers, credentials or private configuration. Review
results omit names, CPFs and raw text but retain scope and employee IDs, which
can still be sensitive with real inputs.

No third-party application source or executable is included. This contribution
does not assert compatibility with an unseen backend or grant rights to other
software. Maintainers decide whether this standalone placement belongs in the
distribution repository; accepting it does not deploy or enable any feature.
