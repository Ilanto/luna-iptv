"""Build the original desktop view; behavior lives in MainWindow."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, icons, theme
from .chips import ChipBar
from .dialogs import text_label
from .guide_view import GuideView
from .home_view import HomeView
from .library import CardGrid, ChannelDelegate
from .logos import LogoCache, LogoViewportController
from .motion import IconButton, LogoMark, MoonSky, NavFrame
from .player import VideoWidget
from .profiles_ui import ProfileAvatar


class VideoStack(QStackedWidget):
    """Video area that keeps a 16:9 shape in the side panel.

    Mini mode swaps in its own size policy and fullscreen switches the shape
    rule off, so the video then fills whatever room it gets.
    """

    def __init__(self):
        super().__init__()
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        # Fullscreen video fills the screen whatever its shape.
        fullscreen = getattr(self.window(), "_fullscreen", False)
        return self.sizePolicy().hasHeightForWidth() and not fullscreen

    def heightForWidth(self, width):
        return max(self.minimumHeight(), round(width * 9 / 16))

    def sizeHint(self):
        return QSize(480, 270)


def button(text, callback, name="", tip=""):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if name:
        widget.setObjectName(name)
    if tip:
        widget.setToolTip(tip)
        widget.setAccessibleName(tip)
    return widget


def icon_button(text, callback, icon=None, name="transport", tip="", **options):
    widget = IconButton(text, icon, **options)
    widget.clicked.connect(callback)
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
    w.sidebar = NavFrame()
    w.sidebar.setObjectName("sidebar")
    w.sidebar.setFixedWidth(96)
    side = QVBoxLayout(w.sidebar)
    side.setContentsMargins(12, 24, 12, 16)
    side.setSpacing(6)
    w.logo_mark = LogoMark(46)
    side.addWidget(w.logo_mark, 0, Qt.AlignHCenter)
    side.addWidget(text_label("LUNA", "railBrand"), 0, Qt.AlignHCenter)
    side.addSpacing(22)
    w.nav_buttons = {}
    for key, title, icon in [
        ("home", "Ana sayfa", "home"),
        ("live", "Canlı TV", "live"),
        ("guide", "Rehber", "guide"),
        ("movie", "Filmler", "movie"),
        ("series", "Diziler", "series"),
        ("favorites", "Favoriler", "star"),
        ("recent", "Geçmiş", "recent"),
    ]:
        b = icon_button(
            title,
            lambda checked=False, k=key: w.set_section(k),
            icon,
            "rail",
            tip={"recent": "Son izlenenler"}.get(key, title),
            stacked=True,
            size=22,
        )
        b.setCheckable(True)
        b.setFixedSize(72, 64)
        b.toggled.connect(lambda on, b=b: on and w.sidebar.indicator.follow(b))
        side.addWidget(b, 0, Qt.AlignHCenter)
        w.nav_buttons[key] = b
    w.nav_buttons["home"].setChecked(True)
    side.addStretch()
    w.profile_button = ProfileAvatar(42)
    w.profile_button.clicked.connect(w.profile_menu)
    side.addWidget(w.profile_button, 0, Qt.AlignHCenter)
    side.addSpacing(6)
    w.add_button = icon_button("Kaynak ekle", w.add_source, "plus", "primary", tip="Kaynak ekle")
    w.add_button.setFixedSize(52, 52)
    side.addWidget(w.add_button, 0, Qt.AlignHCenter)
    w.settings_button = icon_button(
        "Ayarlar", w.open_settings, "gear", "ghost", tip="Ayarlar", size=21
    )
    w.settings_button.setFixedSize(52, 52)
    side.addWidget(w.settings_button, 0, Qt.AlignHCenter)
    w.source_menu_button = icon_button(
        "Kaynak menüsü", w.source_menu, "sliders", "ghost", tip="Kaynak menüsü", size=21
    )
    w.source_menu_button.setFixedSize(52, 52)
    side.addWidget(w.source_menu_button, 0, Qt.AlignHCenter)
    side.addSpacing(8)
    version = text_label(__version__, "faint")
    version.setAlignment(Qt.AlignCenter)
    version.setToolTip(f"Luna IPTV {__version__}")
    side.addWidget(version)
    body.addWidget(w.sidebar)
    w.splitter = QSplitter(Qt.Horizontal)
    w.splitter.setChildrenCollapsible(False)
    body.addWidget(w.splitter, 1)
    w.library = QFrame()
    w.library.setObjectName("library")
    w.library.setMinimumWidth(300)
    pages = QVBoxLayout(w.library)
    pages.setContentsMargins(0, 0, 0, 0)
    w.library_pages = QStackedWidget()
    pages.addWidget(w.library_pages)
    w.browse = QWidget()
    w.library_pages.addWidget(w.browse)
    lib = QVBoxLayout(w.browse)
    lib.setContentsMargins(28, 26, 16, 0)
    lib.setSpacing(14)
    row = QHBoxLayout()
    row.setSpacing(12)
    w.section_title = text_label("Canlı TV", "display")
    row.addWidget(w.section_title)
    w.count_label = text_label("0 yayın", "count")
    w.count_label.setFixedHeight(22)
    row.addWidget(w.count_label, 0, Qt.AlignVCenter)
    row.addStretch()
    w.history_clear_button = icon_button(
        "Geçmişi temizle", w.confirm_clear_history, "trash", "ghost", label=True, size=16
    )
    w.history_clear_button.hide()
    row.addWidget(w.history_clear_button)
    w.search = QLineEdit()
    w.search.setPlaceholderText("Kanal veya içerik ara…")
    w.search.setClearButtonEnabled(True)
    w.search.setAccessibleName("Yayın ara")
    w.search.setMinimumWidth(180)
    w.search.setMaximumWidth(320)
    w.search.addAction(
        QIcon(icons.pixmap("search", theme.TEXT_MUTED, 16, w.devicePixelRatioF())),
        QLineEdit.LeadingPosition,
    )
    row.addWidget(w.search, 1)
    lib.addLayout(row)
    w.search.textChanged.connect(w.filter_changed)
    row = QHBoxLayout()
    row.setSpacing(10)
    w.source_combo = QComboBox()
    w.source_combo.setAccessibleName("Kaynak seç")
    w.source_combo.setMinimumWidth(160)
    w.source_combo.setMaximumWidth(260)
    w.source_combo.currentIndexChanged.connect(w.source_changed)
    row.addWidget(w.source_combo)
    # The combo keeps the chosen category; the chips are how people pick it.
    w.category = QComboBox()
    w.category.setAccessibleName("Kategori")
    w.category.addItem("Tüm kategoriler", "")
    w.category.hide()
    row.addWidget(w.category)
    w.category.currentIndexChanged.connect(w.filter_changed)
    w.category_bar = ChipBar(more_label="Tüm kategoriler")
    w.category_bar.chosen.connect(w.choose_category)
    row.addWidget(w.category_bar, 1)
    w.kind_bar = ChipBar(limit=3, show_counts=True)
    w.kind_bar.chosen.connect(w.choose_search_kind)
    w.kind_bar.hide()
    row.addWidget(w.kind_bar, 1)
    lib.addLayout(row)
    w.folder_row = QWidget()
    folders = QHBoxLayout(w.folder_row)
    folders.setContentsMargins(0, 0, 0, 0)
    folders.setSpacing(10)
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFixedHeight(48)
    w.folder_bar = ChipBar(limit=None, show_counts=True, sort_by_count=False)
    w.folder_bar.setAccessibleName("Favori klasörleri")
    w.folder_bar.chosen.connect(w.choose_folder)
    scroll.setWidget(w.folder_bar)
    folders.addWidget(scroll, 1)
    w.new_folder_button = QPushButton("+ Yeni klasör")
    w.new_folder_button.setObjectName("chipMore")
    w.new_folder_button.setCursor(Qt.PointingHandCursor)
    w.new_folder_button.setFixedHeight(30)
    w.new_folder_button.clicked.connect(lambda: w.create_folder())
    folders.addWidget(w.new_folder_button)
    w.folder_row.hide()
    lib.addWidget(w.folder_row)
    w.channel_list = CardGrid()
    w.channel_list.setObjectName("channels")
    w.channel_list.setAccessibleName("Yayınlar")
    w.channel_list.setMouseTracking(True)
    w.channel_list.setModel(w.proxy)
    w.channel_list.setContextMenuPolicy(Qt.CustomContextMenu)
    w.channel_list.customContextMenuRequested.connect(w.channel_context_menu)
    w.logos = LogoCache(w.store.path, w, size=QSize(240, 96))
    # Shared with the detail card, so a poster decoded for the grid is reused there.
    w.posters = LogoCache(w.store.path, w, size=QSize(240, 360), cache_suffix=".posters")
    w.channel_list.setItemDelegate(
        ChannelDelegate(w.channel_list, logos=w.logos, posters=w.posters, now_for=w.programme_now)
    )
    w.logo_viewport = LogoViewportController(w.channel_list, w.logos, w.posters)
    w.channel_list.clicked.connect(w.activate_index)
    w.channel_list.activated.connect(w.activate_index)
    lib.addWidget(w.channel_list, 1)
    w.no_results = text_label("Henüz yayın yok.\nBir kaynak ekleyerek başla.", "muted")
    w.no_results.setWordWrap(True)
    w.no_results.setAlignment(Qt.AlignCenter)
    lib.addWidget(w.no_results, 1)
    w.guide_view = GuideView(w.logos)
    w.guide_view.watch_channel.connect(w.request_play)
    w.guide_view.remind_programme.connect(w.remind_programme)
    w.guide_view.add_guide.connect(w.configure_guide)
    w.guide_view.show_reminders.connect(w.open_reminders)
    w.library_pages.addWidget(w.guide_view)
    w.home_view = HomeView(w)
    w.library_pages.addWidget(w.home_view)
    w.library_pages.setCurrentWidget(w.home_view)
    w.splitter.addWidget(w.library)
    w.watch = QFrame()
    w.watch.setObjectName("watchPanel")
    w.watch.setMinimumWidth(380)
    view = QVBoxLayout(w.watch)
    w.view_layout = view
    view.setContentsMargins(22, 26, 22, 18)
    view.setSpacing(14)
    w.player_header = QWidget()
    header = QVBoxLayout(w.player_header)
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(4)
    row = QHBoxLayout()
    w.video_badge = text_label("İZLEME ALANI", "eyebrow")
    row.addWidget(w.video_badge)
    row.addStretch()
    w.engine_label = text_label("mpv  ·  yerel oynatıcı", "faint")
    row.addWidget(w.engine_label)
    header.addLayout(row)
    row = QHBoxLayout()
    w.video_title = text_label("İyi bir yayına yer aç.", "title")
    w.video_title.setWordWrap(True)
    row.addWidget(w.video_title, 1)
    w.favorite_button = icon_button("☆", w.toggle_favorite, tip="Favorilere ekle / çıkar", size=22)
    w.favorite_button.setEnabled(False)
    row.addWidget(w.favorite_button, 0, Qt.AlignTop)
    header.addLayout(row)
    w.language_notice = text_label("", "muted")
    w.language_notice.setWordWrap(True)
    w.language_notice.setAccessibleName("Dil tercihi bilgisi")
    w.language_notice.hide()
    header.addWidget(w.language_notice)
    view.addWidget(w.player_header)
    w.video_stack = VideoStack()
    w.video_stack.setMinimumHeight(200)
    empty = MoonSky()
    w.welcome_sky = empty
    welcome = QVBoxLayout(empty)
    welcome.setContentsMargins(20, 14, 20, 16)
    welcome.setSpacing(4)
    welcome.addStretch(3)
    moon = QWidget()
    moon.setFixedSize(90, 76)
    empty.moon_anchor = moon
    welcome.addWidget(moon, 0, Qt.AlignHCenter)
    welcome.addSpacing(10)
    title = text_label("Ekran senin.")
    w.welcome_title = title
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("font-size: 22px; font-weight: 600;")
    welcome.addWidget(title)
    subtitle = text_label("Kendi listeni ekle, sevdiğin yayını seç.", "muted")
    w.welcome_subtitle = subtitle
    subtitle.setWordWrap(True)
    subtitle.setAlignment(Qt.AlignCenter)
    welcome.addWidget(subtitle)
    welcome.addSpacing(10)
    action = icon_button("İlk kaynağını ekle", w.add_source, "plus", "primary", label=True, size=17)
    w.welcome_action = action
    action.setMinimumSize(200, 40)
    welcome.addWidget(action, 0, Qt.AlignHCenter)
    welcome.addStretch(4)
    w.video_stack.addWidget(empty)
    w.video = VideoWidget(w.player, w)
    w.video_stack.addWidget(w.video)
    # No logo motion while video is on screen, even with the pointer on the logo.
    w.video_stack.currentChanged.connect(lambda index: w.logo_mark.set_quiet(index == 1))
    view.addWidget(w.video_stack)
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
    ctrl.setContentsMargins(16, 10, 16, 10)
    ctrl.setSpacing(6)
    w.seek = QSlider(Qt.Horizontal)
    w.seek.setRange(0, 1000)
    w.seek.setEnabled(False)
    w.seek.setAccessibleName("Oynatma konumu")
    w.seek.sliderPressed.connect(lambda: w.transport.cancel(restore_pause=True))
    w.seek.sliderReleased.connect(w.seek_to_slider)
    ctrl.addWidget(w.seek)
    row = QHBoxLayout()
    row.setSpacing(2)
    w.seek_back_button = icon_button(
        "−5 sn",
        lambda: w.transport.seek_relative(-5),
        "back",
        tip="5 saniye geri (←)",
        caption="5",
    )
    row.addWidget(w.seek_back_button)
    w.rewind_button = icon_button(
        "≪", lambda: w.transport.cycle(-1), tip="Geri tara: 2× / 4× / 8× / 16× (J)"
    )
    w.rewind_button.setCheckable(True)
    row.addWidget(w.rewind_button)
    w.play_button = icon_button(
        "▶", w.toggle_play, name="hero", tip="Oynat / duraklat (Boşluk)", size=22
    )
    w.play_button.setFixedSize(46, 46)
    row.addWidget(w.play_button)
    w.forward_button = icon_button(
        "≫", lambda: w.transport.cycle(1), tip="İleri tara: 2× / 4× / 8× / 16× (L)"
    )
    w.forward_button.setCheckable(True)
    row.addWidget(w.forward_button)
    w.seek_forward_button = icon_button(
        "+5 sn",
        lambda: w.transport.seek_relative(5),
        "ahead",
        tip="5 saniye ileri (→)",
        caption="5",
    )
    row.addWidget(w.seek_forward_button)
    w.stop_button = icon_button("■", w.stop_playback, tip="Durdur", size=18)
    row.addWidget(w.stop_button)
    row.addSpacing(6)
    w.time_label = text_label("00:00", "clock")
    w.time_label.setMinimumWidth(90)
    w.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    row.addWidget(w.time_label, 1)
    ctrl.addLayout(row)
    row = QHBoxLayout()
    row.setSpacing(2)
    w.mute_button = icon_button(
        "Ses", lambda: w.player.command(["cycle", "mute"]), tip="Sesi aç / kapat (M)"
    )
    row.addWidget(w.mute_button)
    w.volume = QSlider(Qt.Horizontal)
    w.volume.setRange(0, 100)
    w.volume.setValue(70)
    w.volume.setMinimumWidth(48)
    w.volume.setMaximumWidth(110)
    w.volume.setAccessibleName("Ses seviyesi")
    w.volume.valueChanged.connect(lambda v: w.player.set_property("volume", v))
    row.addWidget(w.volume, 1)
    row.addSpacing(6)
    w.rate_button = button("1×", w.transport.normal_play, "rate", "Normal oynatmaya dön (K)")
    w.rate_button.setMinimumWidth(48)
    w.rate_button.setFixedHeight(26)
    row.addWidget(w.rate_button)
    w.buffer_label = text_label("", "badge")
    w.buffer_label.setAccessibleName("Arabellek durumu")
    w.buffer_label.hide()
    row.addWidget(w.buffer_label)
    row.addStretch()
    w.info_button = icon_button(
        "Bilgi", w.toggle_info_panel, "info", tip="Yayın bilgisini göster / gizle"
    )
    w.info_button.setEnabled(False)
    row.addWidget(w.info_button)
    w.playback_menu_button = icon_button(
        "Oynatma", w.track_menu, "tracks", tip="Ses, altyazı ve oynatma seçenekleri"
    )
    w.playback_menu_button.setProperty("mini_hidden", True)
    row.addWidget(w.playback_menu_button)
    w.mini_button = icon_button("Mini", w.toggle_mini_player, tip="Mini oynatıcıya geç")
    row.addWidget(w.mini_button)
    w.fullscreen_button = icon_button("⛶", w.toggle_fullscreen, tip="Tam ekran (F)")
    row.addWidget(w.fullscreen_button)
    ctrl.addLayout(row)
    for widget in (
        w.rewind_button,
        w.forward_button,
        w.stop_button,
        w.rate_button,
        w.info_button,
        w.playback_menu_button,
    ):
        widget.setProperty("mini_hidden", True)
    w.mini_status = text_label("Hazır.", "muted")
    w.mini_status.setAccessibleName("Oynatma durumu")
    w.mini_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    w.mini_status_row = QWidget()
    mini_status_layout = QHBoxLayout(w.mini_status_row)
    mini_status_layout.setContentsMargins(0, 0, 0, 0)
    mini_status_layout.addWidget(w.mini_status, 1)
    w.mini_cancel_button = button("İptal", w.cancel_recovery, tip="Yeniden bağlanmayı iptal et")
    w.mini_cancel_button.hide()
    mini_status_layout.addWidget(w.mini_cancel_button)
    w.mini_status_row.hide()
    ctrl.addWidget(w.mini_status_row)
    view.addWidget(w.controls)
    w.guide = QFrame()
    w.guide.setObjectName("guide")
    guide = QVBoxLayout(w.guide)
    guide.setContentsMargins(16, 12, 12, 14)
    guide.setSpacing(4)
    row = QHBoxLayout()
    row.setSpacing(8)
    mark = QLabel()
    mark.setPixmap(icons.pixmap("guide", theme.ACCENT, 16, w.devicePixelRatioF()))
    row.addWidget(mark)
    row.addWidget(text_label("PROGRAM REHBERİ", "eyebrow"))
    row.addStretch()
    row.addWidget(
        icon_button("Rehber ekle", w.configure_guide, "plus", "ghost", label=True, size=15)
    )
    guide.addLayout(row)
    w.now_title = text_label("Yayınını seç, akış burada görünsün.")
    w.now_title.setWordWrap(True)
    w.now_title.setStyleSheet("font-weight: 600;")
    guide.addWidget(w.now_title)
    w.next_title = text_label("XMLTV ile şimdi ve sıradaki program.", "muted")
    w.next_title.setWordWrap(True)
    guide.addWidget(w.next_title)
    guide.addStretch()
    view.addWidget(w.guide, 1)
    w.splitter.addWidget(w.watch)
    w.splitter.setStretchFactor(0, 1)
    w.splitter.setStretchFactor(1, 0)
    w.splitter.setSizes([900, 480])
    w.message_bar = QFrame()
    w.message_bar.setObjectName("messageBar")
    bar = QHBoxLayout(w.message_bar)
    bar.setContentsMargins(20, 7, 14, 7)
    w.message = text_label("Hazır. Kaynakların bu bilgisayarda kalır.", "muted")
    w.message.setWordWrap(True)
    bar.addWidget(w.message, 1)
    w.retry_button = icon_button("Yeniden dene", w.retry, "retry", "ghost", label=True, size=16)
    w.retry_button.hide()
    bar.addWidget(w.retry_button)
    w.recovery_cancel_button = button("İptal", w.cancel_recovery)
    w.recovery_cancel_button.setAccessibleName("Otomatik yeniden bağlanmayı iptal et")
    w.recovery_cancel_button.hide()
    bar.addWidget(w.recovery_cancel_button)
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
        ("PgDown", lambda: w.zap(1)),
        ("PgUp", lambda: w.zap(-1)),
    ]:
        shortcut = QShortcut(QKeySequence(key), w)
        shortcut.activated.connect(lambda cb=callback, k=key: w.shortcut_action(k, cb))
