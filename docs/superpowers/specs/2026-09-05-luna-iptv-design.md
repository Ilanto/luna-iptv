# Luna IPTV desktop client design

User-authorized scope: research public IPTV Smarters workflows, independently implement a personal Linux client, test and fix it, deliver an openSUSE package. No third-party source, screenshots, branding, icons or visual assets are copied.

## Product and design
Native desktop library with left navigation (live, movies, series, favorites), source selector, search and category filtering; embedded video and now/next programme information. Local M3U or URL, Xtream login, direct local/HTTP stream, multiple sources and refresh/remove, XMLTV EPG, favorites, recent watching and VOD resume. Series expose provider seasons/episodes. Playback includes pause, seek where supported, volume/mute, audio/subtitle selection and fullscreen. Empty states always describe actual state; demo media is generated locally and explicitly labeled.

User-specified tokens: background #1d2021; accent #e889a8; text #f8e7ec; Hurmit Nerd Font Propo UI, Hurmit Nerd Font Mono code; calm, minimal, modern, desktop oriented. No neon, excessive purple, glow or continuous animations; controlled corner radii and readable contrast. System font fallback when Hurmit is absent. Tokens are centralized in theme.py for maintainable customization; no extra runtime theme editor is needed for this personal client.

## Architecture and alternatives
Recommended: Python 3.11+ / PySide6 Qt Widgets, stdlib SQLite, urllib, XML parsing, libmpv through python-mpv and the OpenGL render API. Native widgets and small testable services keep imports responsive; libmpv renders into a Qt-owned OpenGL framebuffer. Native rendering was tested on GNOME Wayland with DISPLAY unset; no XWayland required. C++/Qt would add compilation requirements without changing the rendering method. Electron/web UI is heavier and has less reliable desktop IPTV codec support. Chosen implementation can be built without privileged changes.

Native Wayland is preferred when WAYLAND_DISPLAY exists; explicit QT_QPA_PLATFORM is respected. QOpenGLWidget owns the GL context and python-mpv resolves procedures through Qt. Render context is freed before asynchronous engine termination. EGL format negotiation remains with Qt. X11 can be selected explicitly as a fallback; XWayland is not a dependency or default.

## Data, IO and security
Dataclass Channel: id, name, url, group, tvg_id, logo, kind (live/movie/series), series_id, headers. Playlist: channels, epg_urls, warnings. Deterministic channel IDs preserve favorites across refresh. SQLite stores channel/source metadata and resume state inside an owner-only XDG data directory. Source credentials and credential-bearing stream URLs are local plaintext, mode 0600 database; never print them. No cloud sync or analytics. No shell interpolation for media paths. Remote playlist references cannot escalate into local-file reads; network requests have timeouts and byte limits; XML accepts unexpanded external DOCTYPE declarations but rejects internal DTD subsets/entities. HTTP is supported for providers requiring it; TLS verification stays enabled. Import/network requests run outside the GUI thread and failures preserve previous library state.

## Acceptance evidence
Automated tests cover malformed/quoted M3U, relative URLs, unsupported schemes, stable IDs, XMLTV time zones and internal DTD/entity rejection, SQLite persistence, refresh/favorites, Xtream endpoints and episodes through local HTTP fixtures. GUI integration tests use Qt and generated FFmpeg test media; actual mpv file-loaded and position advancement, pause/seek/mute/stop are required, not mocked success. Include HLS over localhost. Build RPM and source archive; inspect dependency metadata and extracted launch, desktop/icon installation paths. No provider credentials were supplied: actual provider authentication is validated by fixtures, not claimed live.

Not in this release: recording, catch-up/time-shift, DRM, multi-screen and parental lock. These are explicit scope decisions; core client functionality is complete without them.
