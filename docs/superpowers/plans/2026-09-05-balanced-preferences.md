# Luna IPTV — Tercihler ve izleme geçmişi implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow TDD and review before integration.

**Goal:** Deliver #22 as a complete user-visible part of 0.4.0.
**Architecture:** Existing Qt/SQLite/libmpv architecture; isolate the new state in a small controller/service, keep MainWindow hooks narrow.
**Tech Stack:** Python >=3.11, PySide6 >=6.8,<6.12, python-mpv >=1.0.8,<2; no new runtime dependencies.
**Spec:** docs/superpowers/specs/2026-09-05-balanced-comfort.md

## Global constraints

- Preserve all original 205 tests (including the first 45), source data, native Wayland and the single video context.
- Use temporary data and local media/HTTP only; no provider secrets in output.
- Own branch only; root integrates overlapping MainWindow/storage/layout changes and publishes the tested RPM.
- User explicitly approved this feature scope and the existing merge workflow.

## Deliverable: Tercihler ve izleme geçmişi

**Files:** luna_iptv/preferences.py; luna_iptv/playback_dialogs.py; luna_iptv/storage.py; luna_iptv/window.py; luna_iptv/layout.py
**Tests:** tests/test_preferences.py; tests/test_history.py; tests/test_resume_choice.py
**Interfaces:** Source-scoped preference Store API, semantic TrackPreferences matcher/controller. request_play(channel) handles interactive resume choice then calls play(channel, *, start_override=None, recovering=False). Store.clear_history(source_id=None, reset_progress=False), save_progress(..., mark_recent=True) support preserved positions and cleared visible history.

- [ ] Write focused behavior tests for: same language with changed numeric IDs is selected; missing preferred language uses default without overwriting preference; Off persists after reopen; cancel resume leaves old playback unchanged; restart sets position zero; clear history does not clear favorites or repopulate from active playback.
- [ ] Run those tests and record the missing-feature RED before implementation.
- [ ] Implement the service/controller and the actual UI path; handle cancel, failure and close at each async boundary.
- [ ] Run focused tests and native GUI validation at root's scheduled time. Verify existing relevant tests, lint and format.
- [ ] Commit only owned source/tests; send exact commit and evidence for independent review. Resolve review findings with regression tests.
- [ ] Root merges latest main, runs integration tests, creates the associated PR and merges only the reviewed head.

Example behavioral acceptance (temporary fixtures, literal expected values):
```python
# The implementation-specific tests must assert the user-visible state and stored values.
# same language with changed numeric IDs is selected.
assert result.success is True
assert result.player_count == 1
```
