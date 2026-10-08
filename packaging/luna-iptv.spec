Name:           luna-iptv
Version:        0.17.0
Release:        1
Summary:        Native personal IPTV client for Linux
License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  python3 >= 3.11
Requires:       python3 >= 3.11
Requires:       python3-pyside6 >= 6.8
Requires:       python3-pyside6 < 6.12
Requires:       python3-python-mpv >= 1.0.8
Requires:       python3-python-mpv < 2
Requires:       libmpv2 >= 0.38
Requires:       python3-dbus-python

%description
Luna IPTV is a personal desktop client for M3U playlists, Xtream accounts,
live television, movies, series and XMLTV programme guides. Its Qt interface
uses libmpv to render video within the native Wayland application window.
Users add their own playlists and accounts.

%prep
%setup -q

%build
# Pure Python application: no dependency downloads or compilation are needed.

%install
python3 - <<'PY'
from pathlib import Path
import shutil

destination = Path("%{buildroot}%{_datadir}/%{name}")
for source in Path("luna_iptv").rglob("*.py"):
    target = destination / source
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o644)
PY
install -D -m 0755 packaging/luna-iptv %{buildroot}%{_bindir}/luna-iptv
install -D -m 0644 packaging/luna-iptv.desktop %{buildroot}%{_datadir}/applications/luna-iptv.desktop
for layer in backdrop.png corona.png sparkle.png geometry.json icon-256.png; do
    install -D -m 0644 assets/logo/$layer %{buildroot}%{_datadir}/%{name}/assets/logo/$layer
done
for size in 16 24 32 48 64 128 256 512; do
    install -D -m 0644 assets/logo/icon-$size.png \
        %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/luna-iptv.png
done

%check
python3 -m compileall -q luna_iptv
python3 - <<'PY'
from pathlib import Path
compile(Path("packaging/luna-iptv").read_text(), "packaging/luna-iptv", "exec")
PY
desktop-file-validate packaging/luna-iptv.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/luna-iptv
%{_datadir}/luna-iptv/
%{_datadir}/applications/luna-iptv.desktop
%{_datadir}/icons/hicolor/*/apps/luna-iptv.png

%changelog
* Thu Oct 08 2026 Luna IPTV contributors - 0.17.0-1
- Ana sayfa: continue watching, favourite channels on air, recent channels.
- Sleep timer, next-episode countdown and channel numbers from the keyboard.
- Hide and reorder categories per profile, source and section.

* Thu Oct 08 2026 Luna IPTV contributors - 0.16.0-1
- Profiles with their own favorites, folders, history and reminders.
- Parental PIN for locked categories and channels, settings, sources and backups.
- Category chips that do not fit wait in the full list instead of shrinking.

* Thu Oct 08 2026 Luna IPTV contributors - 0.15.0-1
- Rehber: a full programme guide with days, search and a now line.
- Programme reminders with desktop notifications and an Izle action.
- Backup and restore of sources, favorites, folders, settings and history.

* Wed Oct 07 2026 Luna IPTV contributors - 0.14.0-1
- Category chips with a searchable list, and one search across live TV, films and series.
- Season tabs and a visual episode list with watched marks.
- Favorite folders with a channel context menu.
- Settings: motion level, default audio/subtitle language, start-up action.

* Wed Oct 07 2026 Luna IPTV contributors - 0.13.1-1
- Media controls hear about finished seeks and see position 0 when stopped.
- Resetting watch positions removes progress bars at once.
- The last live channel is found however many films came after it.

* Wed Oct 07 2026 Luna IPTV contributors - 0.13.0-1
- Page Up / Page Down switch to the previous or next live channel while watching.
- Gold progress bars on partly watched films and episodes.
- The last watched live channel waits selected on start-up.
- MPRIS: media keys and GNOME media controls drive playback.

* Fri Oct 02 2026 Luna IPTV contributors - 0.12.2-1
- Release the idle inhibit on pause for players without playlist entry ids.
- Keep a detail card's pending poster when the grid scrolls.
- Fix a missing grid column at widths that divide exactly.

* Thu Oct 01 2026 Luna IPTV contributors - 0.12.1-1
- Keep the sidebar logo still while video is on screen.
- Crop posters in wide favorite and history cards instead of stretching them.

* Thu Oct 01 2026 Luna IPTV contributors - 0.12.0-1
- New eclipse logo: desktop icons from 16 to 512 px and a moving in-app logo.

* Thu Oct 01 2026 Luna IPTV contributors - 0.11.0-1
- Find on IMDb: open the title page when the match is certain, else IMDb search.
- Fullscreen video fills screens of any shape again.

* Thu Oct 01 2026 Luna IPTV contributors - 0.10.0-1
- Poster cards for films and series, with posters prepared at card resolution.
- Redesigned detail card: blurred poster backdrop, facts line, fixed action bar.
- Grid columns refit whenever the window or panels change size.

* Thu Oct 01 2026 Luna IPTV contributors - 0.9.0-1
- Logo wall: channels as wide-logo cards with what is on now and its progress.
- Slim icon rail, side player panel with 16:9 video and upcoming programmes.
- Fix an intermittent crash when closing during playback (mpv update callback).

* Thu Oct 01 2026 Luna IPTV contributors - 0.8.0-1
- New "Gece Ayı" interface: night palette, original line icons and light motion.
- Show the pause control as soon as a new file starts playing.

* Thu Oct 01 2026 Luna IPTV contributors - 0.7.0-1
- Keep the screen awake and block idle suspend while media plays.
- Release the desktop inhibit on pause, stop, removed channels and close.

* Wed Sep 09 2026 Luna IPTV contributors - 0.6.0-1
- Add pre-play audio/subtitle language choices and optional source persistence.
- Apply file-local selection before playback and report missing languages safely.

* Wed Sep 09 2026 Luna IPTV contributors - 0.5.0-1
- Add cached movie and series details with explicit episode metadata/actions.
- Preserve valid metadata on provider authentication and error responses.
