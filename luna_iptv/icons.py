"""Luna's own line icons, drawn on a 24 px grid and tinted at render time."""

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Stroked shapes use the line color; elements marked FILL are solid.
FILL = 'fill="{c}" stroke="none"'

ICONS = {
    "live": '<rect x="2.5" y="7" width="19" height="13.5" rx="3"/><path d="M8 2.5 12 7l4-4.5"/>'
    '<circle cx="17.5" cy="11" r="1.2" ' + FILL + "/>",
    "movie": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7.5 3v18M16.5 3v18'
    'M3 12h18M3 7.5h4.5M16.5 7.5H21M3 16.5h4.5M16.5 16.5H21"/>',
    "series": '<rect x="6.5" y="8" width="14.5" height="12" rx="2.5"/>'
    '<path d="M3 16.5V7a2.5 2.5 0 0 1 2.5-2.5H17"/><path d="M12 11.5v5l4-2.5z" ' + FILL + "/>",
    "star": '<path d="M12 2.8l2.8 5.8 6.4.8-4.7 4.4 1.2 6.4L12 17.1l-5.7 3.1 1.2-6.4-4.7-4.4 '
    '6.4-.8z"/>',
    "star-filled": '<path d="M12 2.8l2.8 5.8 6.4.8-4.7 4.4 1.2 6.4L12 17.1l-5.7 3.1 1.2-6.4-4.7-4.4 '
    '6.4-.8z" fill="{c}"/>',
    "recent": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h4M12 17h8"/><circle cx="15" cy="7" r="2"/>'
    '<circle cx="10" cy="17" r="2"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "play": '<path d="M8 5.6v12.8a1 1 0 0 0 1.5.86l10.3-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 '
    '5.6z" ' + FILL + "/>",
    "pause": '<rect x="6.5" y="5" width="3.8" height="14" rx="1.2" ' + FILL + "/>"
    '<rect x="13.7" y="5" width="3.8" height="14" rx="1.2" ' + FILL + "/>",
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5" ' + FILL + "/>",
    "rewind": '<path d="M11 6.5v11L3.5 12zM20.5 6.5v11L13 12z" ' + FILL + "/>",
    "forward": '<path d="M3.5 6.5v11L11 12zM13 6.5v11l7.5-5.5z" ' + FILL + "/>",
    "back": '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 3.5v4h4"/>',
    "ahead": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 3.5v4h-4"/>',
    "volume": '<path d="M4 9.5v5h3.5l5 4v-13l-5 4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 '
    '0 0 1 0 11"/>',
    "mute": '<path d="M4 9.5v5h3.5l5 4v-13l-5 4z"/><path d="m16.5 9.5 5 5M21.5 9.5l-5 5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r="1.1" '
    + FILL
    + "/>",
    "tracks": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 11h6M15.5 11H17M7 15h3'
    'M12.5 15H17"/>',
    "pip": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><rect x="12" y="11.5" width="6" '
    'height="4.5" rx="1" ' + FILL + "/>",
    "pip-exit": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M15 9l-5 5M10 9.5V14h4.5"/>',
    "fullscreen": '<path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5'
    'a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/>',
    "guide": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4M7.5 '
    '14h3M13.5 14h3M7.5 17.5h3"/>',
    "retry": '<path d="M20 11.5A8 8 0 0 0 5.6 7M4 12.5A8 8 0 0 0 18.4 17"/><path d="M5 3v4h4M19 '
    '21v-4h-4"/>',
    "check": '<path d="m5 12 4.5 4.5L19 7"/>',
    "lock": '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/>'
    '<path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><circle cx="12" cy="15.7" r="1.3" ' + FILL + "/>",
    "unlock": '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/>'
    '<path d="M8 10.5V7.5a4 4 0 0 1 7.6-1.7"/><circle cx="12" cy="15.7" r="1.3" ' + FILL + "/>",
    "user": '<circle cx="12" cy="8.5" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    "gear": '<path d="M9.5 3h5l.6 2.5 1.4.8 2.5-.7 2.5 4.3-1.9 1.8v1.6l1.9 1.8-2.5 4.3'
    "-2.5-.7-1.4.8-.6 2.5h-5l-.6-2.5-1.4-.8-2.5.7-2.5-4.3 1.9-1.8v-1.6L2.5 9.9"
    ' 5 5.6l2.5.7 1.4-.8z"/><circle cx="12" cy="12.5" r="3"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 '
    '1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "moon": '<path d="M20 14.6A8.5 8.5 0 1 1 9.4 4a6.8 6.8 0 0 0 10.6 10.6z" ' + FILL + "/>",
}

# Transport and toggle buttons still speak in their established text states;
# the icon follows the text so behavior code never needs to know about icons.
GLYPHS = {
    "▶": "play",
    "Ⅱ": "pause",
    "■": "stop",
    "≪": "rewind",
    "≫": "forward",
    "☆": "star",
    "★": "star-filled",
    "Ses": "volume",
    "Sessiz": "mute",
    "Mini": "pip",
    "Geri dön": "pip-exit",
    "⛶": "fullscreen",
}


def svg(name, color):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        + ICONS[name].replace("{c}", color)
        + "</svg>"
    )


@lru_cache(maxsize=512)
def pixmap(name, color, size, ratio=1.0):
    """Return a crisp, cached icon pixmap for one color/size/device-ratio combination."""
    pixels = max(1, round(size * ratio))
    image = QPixmap(pixels, pixels)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, color).encode())).render(
        painter, QRectF(0, 0, pixels, pixels)
    )
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image
