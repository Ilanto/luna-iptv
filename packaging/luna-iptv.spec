Name:           luna-iptv
Version:        0.3.0
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
install -D -m 0644 assets/luna-iptv.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/luna-iptv.svg

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
%{_datadir}/icons/hicolor/scalable/apps/luna-iptv.svg

%changelog
