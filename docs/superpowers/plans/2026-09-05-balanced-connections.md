# Luna IPTV — Kaynak bağlantıları implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow TDD and review before integration.

**Goal:** Deliver #20 as a complete user-visible part of 0.4.0.
**Architecture:** Existing Qt/SQLite/libmpv architecture; isolate the new state in a small controller/service, keep MainWindow hooks narrow.
**Tech Stack:** Python >=3.11, PySide6 >=6.8,<6.12, python-mpv >=1.0.8,<2; no new runtime dependencies.
**Spec:** docs/superpowers/specs/2026-09-05-balanced-comfort.md

## Global constraints

- Preserve all original 205 tests (including the first 45), source data, native Wayland and the single video context.
- Use temporary data and local media/HTTP only; no provider secrets in output.
- Own branch only; root integrates overlapping MainWindow/storage/layout changes and publishes the tested RPM.
- User explicitly approved this feature scope and the existing merge workflow.

## Deliverable: Kaynak bağlantıları

**Files:** luna_iptv/source_connections.py; luna_iptv/storage.py; luna_iptv/dialogs.py; luna_iptv/network.py; luna_iptv/window.py
**Tests:** tests/test_source_connections.py
**Interfaces:** SourceConnectionService or small equivalent coordinates candidate validation and atomic Store update; MainWindow.edit_source(source), check_source(source). Existing source id stays stable and channel mapping persists beyond the edit.

- [x] Write focused behavior tests for: old credentials/catalogue/favorites/progress remain after invalid candidate; valid changed server then subsequent refresh preserves matching IDs and changes stream URLs; rollback on DB error; cancelled/deleted source late callback cannot save.
- [x] Run those tests and record the missing-feature RED before implementation.
- [x] Implement the service/controller and the actual UI path; handle cancel, failure and close at each async boundary.
- [x] Run focused tests and native GUI validation at root's scheduled time. Verify existing relevant tests, lint and format.
- [x] Commit only owned source/tests; send exact commit and evidence for independent review. Resolve review findings with regression tests.
- [x] Root merges latest main, runs integration tests, creates the associated PR and merges only the reviewed head.
