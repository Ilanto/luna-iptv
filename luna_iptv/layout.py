"""Build the original desktop view; behavior lives in MainWindow."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .dialogs import text_label
from .library import ChannelDelegate
from .logos import LogoCache, LogoViewportController
from .player import VideoWidget


def button(text, callback, name="", tip=""):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if name:
        widget.setObjectName(name)
    if tip:
        widget.setToolTip(tip)
        widget.setAccessibleName(tip)
    return widget


def build_window(w):
    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    body = QHBoxLayout()
    body.setSpacing(0)
    body.setContentsMargins(0, 0, 0, 0)
    outer.addLayout(body, 1)
    w.sidebar = QFrame()
    w.sidebar.setObjectName("sidebar")
    w.sidebar.setFixedWidth(212)
    side = QVBoxLayout(w.sidebar)
    side.setContentsMargins(18, 26, 18, 20)
    side.setSpacing(9)
    brand = text_label("◔  LUNA", "brand")
    side.addWidget(brand)
    side.addWidget(text_label("KİŞİSEL TELEVİZYON", "eyebrow"))
    side.addSpacing(34)
    side.addWidget(text_label("KÜTÜPHANE", "eyebrow"))
    w.nav_buttons = {}
    for key, title in [
        ("live", "Canlı TV"),
        ("movie", "Filmler"),
        ("series", "Diziler"),
        ("favorites", "Favoriler"),
        ("recent", "Son izlenenler"),
    ]:
        b = button(title, lambda checked=False, k=key: w.set_section(k), "nav")
        b.setCheckable(True)
        side.addWidget(b)
        w.nav_buttons[key] = b
    w.nav_buttons["live"].setChecked(True)
    side.addStretch()
    side.addWidget(text_label("BU BİLGİSAYARDA", "eyebrow"))
    w.source_combo = QComboBox()
    w.source_combo.setAccessibleName("Kaynak seç")
    side.addWidget(w.source_combo)
    w.source_combo.currentIndexChanged.connect(w.source_changed)
    w.add_button = button("+  Kaynak ekle", w.add_source, "primary")
    side.addWidget(w.add_button)
    side.addWidget(button("Kaynak menüsü", w.source_menu))
    side.addSpacing(20)
    side.addWidget(text_label(f"Luna IPTV  /  {__version__}", "muted"))
    body.addWidget(w.sidebar)
    w.splitter = QSplitter(Qt.Horizontal)
    w.splitter.setChildrenCollapsible(False)
    body.addWidget(w.splitter, 1)
    w.library = QFrame()
    w.library.setObjectName("library")
    w.library.setMinimumWidth(265)
    lib = QVBoxLayout(w.library)
    lib.setContentsMargins(16, 24, 16, 16)
    lib.setSpacing(12)
    w.section_title = text_label("Canlı TV", "heading")
    lib.addWidget(w.section_title)
    w.count_label = text_label("0 yayın", "muted")
    lib.addWidget(w.count_label)
    w.search = QLineEdit()
    w.search.setPlaceholderText("Kütüphanede ara…")
    w.search.setClearButtonEnabled(True)
    w.search.setAccessibleName("Yayın ara")
    lib.addWidget(w.search)
    w.search.textChanged.connect(w.filter_changed)
    w.category = QComboBox()
    w.category.setAccessibleName("Kategori")
    w.category.addItem("Tüm kategoriler", "")
    lib.addWidget(w.category)
    w.category.currentIndexChanged.connect(w.filter_changed)
    w.back_button = button("←  Dizilere dön", lambda: w.set_section("series"))
    w.back_button.hide()
    lib.addWidget(w.back_button)
    w.channel_list = QListView()
    w.channel_list.setObjectName("channels")
    w.channel_list.setAccessibleName("Yayınlar")
    w.channel_list.setMouseTracking(True)
    w.channel_list.setUniformItemSizes(True)
    w.channel_list.setModel(w.proxy)
    w.logos = LogoCache(w.store.path, w)
    w.channel_list.setItemDelegate(ChannelDelegate(w.channel_list, logos=w.logos))
    w.logo_viewport = LogoViewportController(w.channel_list, w.logos)
    w.channel_list.clicked.connect(w.activate_index)
    w.channel_list.activated.connect(w.activate_index)
    lib.addWidget(w.channel_list, 1)
    w.no_results = text_label("Henüz yayın yok.\nBir kaynak ekleyerek başla.", "muted")
    w.no_results.setWordWrap(True)
    w.no_results.setAlignment(Qt.AlignCenter)
    lib.addWidget(w.no_results, 1)
    w.splitter.addWidget(w.library)
    w.watch = QWidget()
    view = QVBoxLayout(w.watch)
    w.view_layout = view
    view.setContentsMargins(25, 24, 25, 20)
    view.setSpacing(14)
    w.player_header = QWidget()
    header = QVBoxLayout(w.player_header)
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(14)
    row = QHBoxLayout()
    w.video_badge = text_label("İZLEME ALANI", "eyebrow")
    row.addWidget(w.video_badge)
    row.addStretch()
    w.engine_label = text_label("mpv  ·  yerel oynatıcı", "muted")
    row.addWidget(w.engine_label)
    header.addLayout(row)
    row = QHBoxLayout()
    w.video_title = text_label("İyi bir yayına yer aç.", "heading")
    w.video_title.setWordWrap(True)
    row.addWidget(w.video_title, 1)
    w.favorite_button = button("☆", w.toggle_favorite, tip="Favorilere ekle / çıkar")
    w.favorite_button.setEnabled(False)
    row.addWidget(w.favorite_button)
    header.addLayout(row)
    view.addWidget(w.player_header)
    w.video_stack = QStackedWidget()
    w.video_stack.setMinimumHeight(230)
    w.video_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    empty = QFrame()
    empty.setStyleSheet(
        "QFrame { background: #17191a; border: 1px solid #363435; border-radius: 8px; } QLabel { border: none; }"
    )
    welcome = QVBoxLayout(empty)
    welcome.setContentsMargins(30, 24, 30, 24)
    welcome.addStretch()
    moon = text_label("◔")
    moon.setAlignment(Qt.AlignCenter)
    moon.setStyleSheet("font-size: 64px; color: #e889a8;")
    icon_path = Path(__file__).resolve().parents[1] / "assets" / "luna-iptv.svg"
    if not icon_path.exists():
        icon_path = Path("/usr/share/icons/hicolor/scalable/apps/luna-iptv.svg")
    if icon_path.exists():
        moon.setPixmap(
            QPixmap(str(icon_path)).scaled(84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
    welcome.addWidget(moon)
    title = text_label("Ekran senin.")
    w.welcome_title = title
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("font-size: 27px;")
    welcome.addWidget(title)
    subtitle = text_label(
        "Kendi listeni ekle. Sevdiğin yayını seç.\nGerisini Luna’ya bırak.", "muted"
    )
    w.welcome_subtitle = subtitle
    subtitle.setWordWrap(True)
    subtitle.setAlignment(Qt.AlignCenter)
    welcome.addWidget(subtitle)
    welcome.addSpacing(14)
    action = button("İlk kaynağını ekle", w.add_source, "primary")
    w.welcome_action = action
    action.setMaximumWidth(230)
    welcome.addWidget(action, 0, Qt.AlignHCenter)
    welcome.addStretch()
    w.video_stack.addWidget(empty)
    w.video = VideoWidget(w.player, w)
    w.video_stack.addWidget(w.video)
    view.addWidget(w.video_stack, 1)
    w.info_panel = QFrame()
    w.info_panel.setObjectName("mediaInfo")
    info = QGridLayout(w.info_panel)
    info.setContentsMargins(12, 9, 12, 9)
    info.setHorizontalSpacing(10)
    info.setVerticalSpacing(5)
    info.setColumnStretch(1, 1)
    info.setColumnStretch(3, 1)
    info.addWidget(text_label("YAYIN BİLGİSİ", "eyebrow"), 0, 0, 1, 4)

    def info_field(attribute, title, row, column):
        info.addWidget(text_label(title, "eyebrow"), row, column)
        value = text_label("Bilgi yok", "muted")
        value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        value.setMinimumWidth(0)
        value.setWordWrap(True)
        value.setAccessibleName(title.title())
        setattr(w, attribute, value)
        info.addWidget(value, row, column + 1)

    info_field("info_dimensions", "BOYUT", 1, 0)
    info_field("info_quality", "KALİTE", 1, 2)
    info_field("info_video_codec", "VİDEO", 2, 0)
    info_field("info_audio_codec", "SES", 2, 2)
    info_field("info_audio_layout", "KANALLAR", 3, 0)
    info_field("info_fps", "KARE HIZI", 3, 2)
    info_field("info_bitrate", "BİT HIZI", 4, 0)
    info_field("info_dynamic_range", "KAYNAK ARALIĞI", 4, 2)
    w.info_panel.hide()
    view.addWidget(w.info_panel)
    w.controls = QFrame()
    w.controls.setObjectName("controls")
    ctrl = QVBoxLayout(w.controls)
    ctrl.setContentsMargins(12, 9, 12, 9)
    ctrl.setSpacing(4)
    w.seek = QSlider(Qt.Horizontal)
    w.seek.setRange(0, 1000)
    w.seek.setEnabled(False)
    w.seek.setAccessibleName("Oynatma konumu")
    w.seek.sliderPressed.connect(lambda: w.transport.cancel(restore_pause=True))
    w.seek.sliderReleased.connect(w.seek_to_slider)
    ctrl.addWidget(w.seek)
    row = QHBoxLayout()
    row.setSpacing(6)
    w.seek_back_button = button(
        "−5 sn", lambda: w.transport.seek_relative(-5), "transport", "5 saniye geri (←)"
    )
    row.addWidget(w.seek_back_button)
    w.rewind_button = button(
        "≪", lambda: w.transport.cycle(-1), "transport", "Geri tara: 2× / 4× / 8× / 16× (J)"
    )
    w.rewind_button.setCheckable(True)
    row.addWidget(w.rewind_button)
    w.play_button = button("▶", w.toggle_play, "transport", "Oynat / duraklat (Boşluk)")
    row.addWidget(w.play_button)
    w.forward_button = button(
        "≫", lambda: w.transport.cycle(1), "transport", "İleri tara: 2× / 4× / 8× / 16× (L)"
    )
    w.forward_button.setCheckable(True)
    row.addWidget(w.forward_button)
    w.seek_forward_button = button(
        "+5 sn", lambda: w.transport.seek_relative(5), "transport", "5 saniye ileri (→)"
    )
    row.addWidget(w.seek_forward_button)
    row.addWidget(button("■", w.stop_playback, "transport", "Durdur"))
    w.rate_button = button("1×", w.transport.normal_play, "transport", "Normal oynatmaya dön (K)")
    w.rate_button.setMinimumWidth(54)
    row.addWidget(w.rate_button)
    row.addStretch()
    w.time_label = text_label("00:00", "muted")
    w.time_label.setFont(
        QFont("Hurmit Nerd Font Mono", 9)
        if "Hurmit Nerd Font Mono" in QFontDatabase.families()
        else QFontDatabase.systemFont(QFontDatabase.FixedFont)
    )
    row.addWidget(w.time_label)
    ctrl.addLayout(row)
    row = QHBoxLayout()
    row.setSpacing(6)
    w.buffer_label = text_label("", "badge")
    w.buffer_label.setAccessibleName("Arabellek durumu")
    w.buffer_label.hide()
    row.addWidget(w.buffer_label)
    row.addStretch()
    w.mute_button = button(
        "Ses", lambda: w.player.command(["cycle", "mute"]), tip="Sesi aç / kapat (M)"
    )
    row.addWidget(w.mute_button)
    w.volume = QSlider(Qt.Horizontal)
    w.volume.setRange(0, 100)
    w.volume.setValue(70)
    w.volume.setFixedWidth(82)
    w.volume.setAccessibleName("Ses seviyesi")
    w.volume.valueChanged.connect(lambda v: w.player.set_property("volume", v))
    row.addWidget(w.volume)
    w.info_button = button("Bilgi", w.toggle_info_panel, tip="Yayın bilgisini göster / gizle")
    w.info_button.setEnabled(False)
    row.addWidget(w.info_button)
    row.addWidget(button("A / S", w.track_menu, tip="Ses parçası ve altyazı seç"))
    w.fullscreen_button = button("⛶", w.toggle_fullscreen, tip="Tam ekran (F)")
    row.addWidget(w.fullscreen_button)
    ctrl.addLayout(row)
    view.addWidget(w.controls)
    w.guide = QFrame()
    w.guide.setObjectName("guide")
    guide = QVBoxLayout(w.guide)
    guide.setContentsMargins(0, 14, 0, 0)
    guide.setSpacing(6)
    row = QHBoxLayout()
    row.addWidget(text_label("PROGRAM REHBERİ", "eyebrow"))
    row.addStretch()
    row.addWidget(button("Rehber ekle", w.configure_guide))
    guide.addLayout(row)
    w.now_title = text_label("Yayınını seç, akış burada görünsün.")
    w.now_title.setWordWrap(True)
    guide.addWidget(w.now_title)
    w.next_title = text_label("XMLTV ile şimdi ve sıradaki program.", "muted")
    w.next_title.setWordWrap(True)
    guide.addWidget(w.next_title)
    view.addWidget(w.guide)
    w.splitter.addWidget(w.watch)
    w.splitter.setStretchFactor(0, 0)
    w.splitter.setStretchFactor(1, 1)
    w.splitter.setSizes([310, 780])
    w.message_bar = QFrame()
    bar = QHBoxLayout(w.message_bar)
    bar.setContentsMargins(18, 8, 18, 8)
    w.message = text_label("Hazır. Kaynakların bu bilgisayarda kalır.", "muted")
    w.message.setWordWrap(True)
    bar.addWidget(w.message, 1)
    w.retry_button = button("Yeniden dene", w.retry)
    w.retry_button.hide()
    bar.addWidget(w.retry_button)
    outer.addWidget(w.message_bar)
    w.setCentralWidget(root)
    for key, callback in [
        ("Ctrl+O", w.add_source),
        ("Ctrl+F", lambda: w.search.setFocus()),
        ("Space", w.toggle_play),
        ("F", w.toggle_fullscreen),
        ("M", lambda: w.player.command(["cycle", "mute"])),
        ("Escape", w.leave_fullscreen),
        ("Right", lambda: w.transport.seek_relative(5)),
        ("Left", lambda: w.transport.seek_relative(-5)),
        ("J", lambda: w.transport.cycle(-1)),
        ("L", lambda: w.transport.cycle(1)),
        ("K", w.transport.normal_play),
    ]:
        shortcut = QShortcut(QKeySequence(key), w)
        shortcut.activated.connect(lambda cb=callback, k=key: w.shortcut_action(k, cb))
