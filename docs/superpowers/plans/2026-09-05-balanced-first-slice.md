# Luna IPTV Balanced First Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Checkboxes track completion.

**Goal:** Deliver the user's approved seven daily-use improvements without slowing channel changes or changing the native Wayland renderer.

**Architecture:** Keep one Player/VideoWidget and SQLite library. Add small independent logo, media-info and account-profile components; preserve existing parser/storage/UI contracts. Integrate feature branches through reviewed PRs, with synthetic tests and bounded background work.

**Tech Stack:** Python >=3.11, PySide6 >=6.8,<6.12, python-mpv >=1.0.8,<2, libmpv >=0.38; no additional runtime dependency.

**Spec:** docs/superpowers/specs/2026-09-05-balanced-first-slice.md

## Global Constraints

- Preserve all original 45 tests, existing provider classification and user data.
- Native GNOME Wayland, one libmpv handle; never require XWayland.
- No real provider/account/database access in tests; use tmp_path and localhost.
- Do not log credentials or credential-bearing URLs; keep artifacts under ignored work/.
- Colours #1d2021, #e889a8, #f8e7ec; calm Qt desktop UI, Hurmit Propo/Mono.
- Separate issue/branch/PR per independently reviewable feature; root coordinates push and merge.
- User authorized implementation, push and merge after checks; no repeated approval menu.
- Isolated worktrees permit parallel independent implementation; merge overlapping UI edits sequentially and test the combined tree.

## Task 1: Recent ordering

**Files:** luna_iptv/library.py, luna_iptv/window.py, tests/test_recent.py.

**Interfaces:** Store.recent_ids() remains ordered list[str]; ChannelFilter.set_recent_ids(ids) stores ranks, preserves O(1) membership, and invalidates affected sorting. refresh() enables sorting only in the recent root view and resets to source order elsewhere.

- [ ] Run prepared real-UI regression tests and verify ordering failures.
- [ ] Replace set conversion with ordered ranks, implement lessThan and restore sort(-1) outside recent; leave DB schema and playback untouched.
- [ ] Verify filters, refresh, ordinary sections and empty history; run original suite.
- [ ] Commit, obtain independent review, PR closing existing issue #1, merge after checks.

```python
assert visible_names(recent_view) == ['Most recently watched', 'Earlier watch']
window.set_section('live')
assert visible_names(window.channel_list.model()) == ['Catalogue first', 'Catalogue second']
```

## Task 2: Source rename

**Files:** luna_iptv/storage.py, luna_iptv/window.py, tests/test_source_rename.py.

**Interfaces:** Store.rename_source(source_id: str, name: str) -> bool; trim names, reject empty/control-invalid input, return false for absent source. UI uses a prefilled rename dialog from source menu and updates the source picker without resetting active playback or list filters.

- [ ] Test rename persists across reopen, preserves every credential/URL/channel/favorite/progress field, cancellation and empty/unknown source.
- [ ] Implement narrow UPDATE sources SET name; add menu action and accessible dialog.
- [ ] Test UI-selected source identity and playing channel stay stable; original suite.
- [ ] Commit, review and merge associated PR.

```python
assert store.rename_source(source_id, '  Evdeki yayınlar  ')
assert store.sources()[0]['name'] == 'Evdeki yayınlar'
assert store.progress(channel_id) == (42, 100)
```

## Task 3: Search performance

**Files:** luna_iptv/library.py, tests/test_search.py, scripts/benchmark-search.py.

**Interfaces:** ChannelModel.reset() precomputes normalized name/group keys once; ChannelFilter.refresh() normalizes query once. Existing immediate textChanged behaviour is retained; no debounce delay that breaks existing UI contracts.

- [ ] Add accented/Turkish name/group, empty query and refresh correctness regressions.
- [ ] Measure current 10k/50k/100k filter path; cache derived search keys and query.
- [ ] Verify updated catalogues replace stale keys, source/category/recent filters still compose; report relative time and memory tradeoff without flaky timing assertions.
- [ ] Run suite, commit, review and merge associated PR.

```python
window.search.setText('ISIK')
assert visible_names(window.channel_list.model()) == ['İkinci IŞIK yayını']
```

## Task 4: Logos and bounded cache

**Files:** create luna_iptv/logos.py and tests/test_logos.py; integrate library.py, layout.py/window.py, playlist.py/network.py as needed.

**Interfaces:** A QObject cache/controller owns requests and emits ready notifications. The delegate only reads a prepared pixmap, then paints the existing initials if unavailable. A viewport controller schedules visible rows after scroll/model/filter changes. Resolve relative logo paths in import; all remote-origin logo URLs remain HTTP(S).

**Bounds:** At most 4 concurrent fetches, 5-second request timeout, 2 MiB transfer limit, 4-million-pixel decode limit; downscale logos to list size; memory LRU at most 256 entries and disk quota 64 MiB. Success TTL 7 days, negative TTL 15 minutes. Cache path must derive from isolated Store data for tests; files private. Implement deterministic injectable clock/limits where helpful.

- [ ] Write localhost tests for valid image, relative M3U/Xtream paths, missing/broken/oversized image, duplicate request, cache hit/restart/expiry/eviction and negative caching.
- [ ] Implement bounded asynchronous fetch/decode and disk writes outside paint; no credential-header forwarding; discard stale/closed view updates.
- [ ] Wire visible-row loading, aspect-preserving logo draw and fallback. Verify no stream is opened by logo work and shutdown has no late QObject errors.
- [ ] Run focused tests and lint; commit to assigned isolated branch. Root reviews, merges current main into branch, runs integrated suite and merges PR.

```python
request_logo(local_png_url)
wait_for_ready()
assert prepared_logo(local_png_url) is not None
assert http_server.request_count == 1
request_logo(local_png_url)
assert http_server.request_count == 1
```

## Task 5: Playback information and buffer indicator

**Files:** create luna_iptv/media_info.py, tests/test_media_info.py; modify player.py observers, layout.py/window.py UI integration.

**Interfaces:** A small MediaInfo state consumes property updates and resets on new load/stop; a compact optional panel uses plain-text labels. Observe optional mpv properties defensively: video-dec-params/video-params, container-fps, video/audio-bitrate, audio-params and selected track metadata. Existing single handle/render lifecycle stays unchanged. Keep static fields event-driven; throttle dynamic visible detail updates.

- [ ] Test actual dimensions and SD/HD/QHD/4K labels, interlaced/unknown scan, selected codecs/audio layout, FPS, absent bitrate, HDR/SDR/unknown and resets.
- [ ] Test buffer 0/partial/full/unknown, loading/paused/idle transitions, no stale label after switching stream, and controls working in fullscreen.
- [ ] Implement state, panel toggle/accessible labels and indicator from already-observed cache-buffering-state/paused-for-cache; no new network request/decoder.
- [ ] Verify against local generated H264/AAC and MPEG2/MP2 data; commit isolated branch. Root integrates/reviews/runs native suite and merges PR.

```python
state.update('video-params', {'w': 1920, 'h': 1080})
assert state.dimensions == '1920 × 1080'
state.reset()
assert state.dimensions == 'Bilgi yok'
```

## Task 6: Xtream account status

**Files:** create luna_iptv/accounts.py and tests/test_accounts.py; modify models.py/network.py/storage.py and window.py (dedicated account dialog/module preferred).

**Interfaces:** An optional sanitized account-profile value on Playlist (default None preserves all three-argument constructors); XtreamClient.account_info() retrieves only the profile endpoint. Add an FK-cascading account snapshot table instead of storing the raw response. Account dialog shows cached data immediately and refreshes asynchronously without reloading the catalogue.

- [ ] Test mixed string/number timestamps/counts, auth versus status, expired/disabled/unknown states, missing dates and zero limits. Only whitelist metadata; never retain returned username/password/token.
- [ ] Test cached profile persistence on old/new Store, source deletion cascade, stale async reply after deletion/close and refresh network error retaining last known data with timestamp.
- [ ] Capture profile already returned during catalogue loading. Show account-created date, expiry, remaining days/approximate months, text+visual status, last checked active/max connections. Do not equate network failure with expired or null with unlimited.
- [ ] Add source menu access for Xtream only and separate refresh action; focused tests/lint; commit isolated branch. Root integrates/reviews/tests and merges PR.

```python
profile = normalize_profile({'user_info': {'auth': 1, 'status': 'Expired', 'password': 'fixture-secret'}})
assert profile.status == 'expired'
assert 'fixture-secret' not in serialize_profile(profile)
```

## Task 7: Integrated release verification

**Files:** scripts/ benchmark/verification helpers; README.md, docs/verification.md, versions in pyproject.toml/__init__.py/spec and scripts/check-package.py as required.

- [ ] Run all original and added tests, Ruff and format checks on the combined exact PR head.
- [ ] Run native Wayland local/HLS + H264 probe and full GUI smoke using only synthetic isolated data.
- [ ] Measure search and repeated local channel switches before/after with the same harness; investigate material regressions before merge.
- [ ] Visually inspect synthetic-source UI with logos, account panel and playback info; verify fallback and accessibility labels.
- [ ] Build version 0.2.0 RPM without system install; extract, compare packaged source bytes and launch on native Wayland with isolated data.
- [ ] Review complete diff, scan staged/history content for secrets/artifacts, merge final release PR, verify remote main hash and issue closure. Leave original checkpoint branch/history intact.

## Execution record

Per-task progress, decisions and review outcomes live in this plan's ignored SDD ledger; GitHub issues/PRs retain durable completion evidence.
