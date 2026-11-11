# GoogleTest clang-tidy validation design

Approved 2026-11-11: configure direct and pre-commit workflows in openlibing-test/googletest, manual triggers only, OIDC upload to beta. Reuse PR 59 wrapper and demo binary/license. Enable GoogleTest/GoogleMock tests and samples to measure real compilation-database coverage. Retain SARIF, compile database, scan status and logs; distinguish findings, execution errors, OBS upload, callback and platform ingestion.

Options considered: direct only (less coverage of documentation); both documented modes (selected); local only (cannot verify GitHub OIDC/upload).

# GoogleTest clang-tidy Implementation Plan

> Execute the approved tasks sequentially in the existing clean checkout on verify/clang-tidy-docs-59.

**Goal:** Verify PR 59 instructions against a real C++ repository.
**Architecture:** Two manual GitHub workflows share the documented wrapper, clang-tidy rules, vendored converter and pre-commit hook. Scan all translation units in a CMake database with tests/samples enabled. Preserve outputs as GitHub artifacts and local evidence.
**Tech Stack:** CMake, clang-tidy, Python, pre-commit, GitHub Actions, SARIF, openLiBing OIDC upload.

1. Copy wrapper from docs commit 8923abb1d5436e8eb44f1a4a63f840f804f037ea and converter/license/config from demo; record hashes.
2. Create both workflows with workflow_dispatch only, CMake test/sample options enabled, report inspection and artifact retention. Start with documented upload-sarif-action@main; record any changes needed to work.
3. Validate YAML and Python, check tracked executable mode and diff, commit and push configuration. Publish workflows to default branch for manual dispatch; preserve existing workflows.
4. Dispatch both workflows, follow to terminal state and download evidence. Verify database coverage, diagnostic results and errors, upload and callback status. Fix demonstrated integration failures, rerun as required, preserve first-run evidence.
5. Write evidence-based Chinese reliability report with run links, commit hashes, counts, necessary adaptations and limits of verification.
