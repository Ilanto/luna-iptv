# Public product research and original architecture

Research date: 2026-09-05. Only publicly documented functionality was reviewed. No Smarters source code, screenshots, icons, visual layouts or other proprietary assets were imported.

## What informed the information architecture

The public Smarters Pro product pages describe playlist-based television, live/VOD libraries, films and series, multiple lists, favorites and parental features. The WHMCS Smarters developer's Lite listing also documents M3U/JSON and EPG history. Lite and current Pro have different store listings and are not assumed to have identical versions or features.

- [Smarters Pro features](https://smarterspro.com/#features)
- [Smarters Pro about](https://smarterspro.com/about-us/)
- [WHMCS Smarters Player Lite developer listing](https://apps.apple.com/us/app/smarters-player-lite/id1628995509)
- [Current Smarters Pro listing](https://apps.apple.com/us/app/smarters-pro/id6450746159)

Luna independently implements source → content kind → category/search → playback, with an adjacent now/next guide. Xtream compatibility is our client feature, validated with a local server fixture; this research did not find a current official Xtream contract supplied by Smarters. No feature parity claim is made. Recording, multi-screen, catch-up and parental lock are outside this version.

## Native Linux choice

Qt Widgets gives native desktop interaction, input and accessibility infrastructure without a browser process. Python services separate network IO, parsing and persistence. libmpv render API targets the Qt-owned OpenGL framebuffer, so it does not need to nest a foreign X11 window. Official mpv documentation recommends the render API as a general integration method. The Python binding is an independently licensed dependency, not code taken from Smarters.

- [mpv integration methods and render API](https://github.com/mpv-player/mpv-examples/blob/master/libmpv/README.md)
- [mpv commands, properties and options](https://mpv.io/manual/master/)
- [python-mpv upstream](https://github.com/jaseg/python-mpv)
- [Qt for Python installation](https://doc.qt.io/qtforpython-6/gettingstarted.html)

Initial X11 window-embedding proposal was replaced after the user's request to verify native Wayland first. Local evidence confirmed native Qt EGL + libmpv with DISPLAY unset. Qt's negotiated EGL context must be preserved; forcing a late GL3.3 Core profile mismatched this machine's shared context. No XWayland requirement remains in the implementation or RPM.

## openSUSE evidence

Factory package sources and live local package metadata identify `python3-pyside6`, `python3-python-mpv` and `libmpv2` as runtime dependencies. The Python package is noarch, with native code supplied by system packages.

- [openSUSE PySide6 spec](https://api.opensuse.org/public/source/openSUSE:Factory/python3-pyside6/python3-pyside6.spec)
- [openSUSE mpv spec](https://api.opensuse.org/public/source/openSUSE:Factory/mpv/mpv.spec)
- [openSUSE python-mpv package](https://software.opensuse.org/package/python-python-mpv)

Missing local test runtime libraries were downloaded from the configured official openSUSE repo-oss using zypper, verified by RPM signatures, and extracted under work/deps. They are not bundled in the RPM or source release. The development machine's system package state was not changed.

## XMLTV compatibility

The [upstream XMLTV DTD](https://raw.githubusercontent.com/XMLTV/xmltv/master/xmltv.dtd) specifies UTC when a timestamp has no timezone. Luna follows that default, accepts ordinary external DOCTYPE metadata without fetching it, and rejects internal subsets/entities (including UTF-16 declarations). Listings without a stop time are currently omitted from the now/next view.
