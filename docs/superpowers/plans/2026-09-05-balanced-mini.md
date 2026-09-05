# Luna IPTV — Mini player implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow TDD and review before integration.

**Goal:** Deliver #23 as a complete user-visible part of 0.4.0.
**Architecture:** Existing Qt/SQLite/libmpv architecture; isolate the new state in a small controller/service, keep MainWindow hooks narrow.
**Tech Stack:** Python >=3.11, PySide6 >=6.8,<6.12, python-mpv >=1.0.8,<2; no new runtime dependencies.
**Spec:** docs/superpowers/specs/2026-09-05-balanced-comfort.md

## Global constraints

- Preserve all original 205 tests (including the first 45), source data, native Wayland and the single video context.
- Use temporary data and local media/HTTP only; no provider secrets in output.
- Own branch only; root integrates overlapping MainWindow/storage/layout changes and publishes the tested RPM.
- User explicitly approved this feature scope and the existing merge workflow.

## Deliverable: Mini player

**Files:** luna_iptv/mini_player.py; luna_iptv/fullscreen.py; luna_iptv/layout.py; luna_iptv/window.py
**Tests:** tests/test_mini_player.py
**Interfaces:** MiniPlayerController(window) owns normal geometry/minimum-size/visibility snapshot; MainWindow.toggle_mini_player and leave_mini_player integrate explicit mode transitions without replacing video parent/context. Own show/hide snapshot must preserve info invalidation and controls added by other slices.

- [ ] Write focused behavior tests for: mini window is compact but video framebuffer stays valid; context/player/parent identity unchanged; controls usable; restore normal geometry/minimum and visibility; repeated mini/fullscreen transitions and close preserve renderer lifecycle.
- [ ] Run those tests and record the missing-feature RED before implementation.
- [ ] Implement the service/controller and the actual UI path; handle cancel, failure and close at each async boundary.
- [ ] Run focused tests and native GUI validation at root's scheduled time. Verify existing relevant tests, lint and format.
- [ ] Commit only owned source/tests; send exact commit and evidence for independent review. Resolve review findings with regression tests.
- [ ] Root merges latest main, runs integration tests, creates the associated PR and merges only the reviewed head.
