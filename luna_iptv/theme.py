"""Luna "Gece Ayı" design tokens: a deep night sky lit by soft moonlight."""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

NIGHT = "#0b1020"  # window
DUSK = "#0f1529"  # sidebar and quiet panels
SURFACE = "#141b33"  # cards
RAISED = "#1b2444"  # inputs and buttons
HOVER = "#232e55"
LINE = "#26315a"
LINE_SOFT = "#1c2547"
ACCENT = "#a9b8ff"  # moonlight
ACCENT_STRONG = "#cdd6ff"
ACCENT_INK = "#0b1020"
ACCENT_TINT = "#222d5c"
GOLD = "#ffd58a"  # favorites and the moon's warm halo
TEXT = "#e8ecff"
TEXT_SOFT = "#a3acd4"
TEXT_MUTED = "#6e78a6"
DANGER = "#ff9aa8"

UI_FAMILIES = ("Poppins", "Adwaita Sans", "Cantarell", "Noto Sans")
MONO_FAMILIES = ("Hurmit Nerd Font Mono", "Adwaita Mono")
# Luna's typewriter voice stays on the brand, small section labels and the clock.
ACCENT_FAMILIES = ("Hurmit Nerd Font Propo", "Hurmit Nerd Font", "Adwaita Mono")


def family(candidates, fallback=""):
    available = set(QFontDatabase.families())
    return next((name for name in candidates if name in available), fallback)


def mono_font(size=9):
    name = family(MONO_FAMILIES)
    if name:
        return QFont(name, size)
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(size)
    return font


def apply_theme(app):
    app.setStyle("Fusion")
    font = QFont(family(UI_FAMILIES, "Sans Serif"), 10)
    font.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(font)
    palette = QPalette()
    for role, color in [
        (QPalette.Window, NIGHT),
        (QPalette.WindowText, TEXT),
        (QPalette.Base, DUSK),
        (QPalette.AlternateBase, SURFACE),
        (QPalette.Text, TEXT),
        (QPalette.Button, RAISED),
        (QPalette.ButtonText, TEXT),
        (QPalette.Highlight, ACCENT_TINT),
        (QPalette.HighlightedText, TEXT),
        (QPalette.ToolTipBase, RAISED),
        (QPalette.ToolTipText, TEXT),
        (QPalette.PlaceholderText, TEXT_MUTED),
        (QPalette.Link, ACCENT),
    ]:
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_MUTED))
    app.setPalette(palette)
    # Desktop platform themes assign their own font per widget class, which
    # QApplication.setFont() does not replace; the style sheet always wins.
    rules = [f'QWidget {{ font-family: "{font.family()}"; }}']
    voice = family(ACCENT_FAMILIES)
    if voice:
        rules.append(
            f'QLabel#brand, QLabel#railBrand, QLabel#eyebrow {{ font-family: "{voice}"; }}'
        )
    clock = mono_font()
    rules.append(f'QLabel#clock {{ font-family: "{clock.family()}"; font-size: 9pt; }}')
    app.setStyleSheet(STYLE + "\n".join(rules))


STYLE = f"""
QMainWindow, QDialog {{ background: {NIGHT}; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 19px; font-weight: 600; letter-spacing: 5px; color: {TEXT}; }}
QLabel#eyebrow {{ color: {TEXT_MUTED}; font-size: 9px; font-weight: 600; letter-spacing: 2px; }}
QLabel#heading {{ font-size: 24px; font-weight: 600; }}
QLabel#display {{ font-size: 30px; font-weight: 700; }}
QLabel#railBrand {{ color: {TEXT_SOFT}; font-size: 9px; font-weight: 700; letter-spacing: 3px; }}
QLabel#title {{ font-size: 18px; font-weight: 600; }}
QLabel#muted {{ color: {TEXT_SOFT}; }}
QLabel#faint {{ color: {TEXT_MUTED}; font-size: 9px; }}
QLabel#facts {{ color: {ACCENT_STRONG}; font-weight: 500; }}
QLabel#lead {{ color: {TEXT_SOFT}; font-size: 11pt; line-height: 150%; }}
QLabel#count {{ color: {ACCENT}; background: {ACCENT_TINT}; border-radius: 8px;
    padding: 2px 9px; font-size: 9px; font-weight: 600; }}
QLabel#clock {{ color: {TEXT_SOFT}; }}
QLabel#badge {{ color: {ACCENT}; font-size: 10px; }}
QFrame#watchNotice {{ background: {ACCENT_TINT}; border: 1px solid {LINE};
    border-radius: 12px; }}
QFrame#watchNotice QLabel {{ color: {TEXT}; font-weight: 600; }}
QLabel#error {{ color: {DANGER}; }}
QLabel#pinBadge {{ background: {DUSK}; border: 1px solid {LINE}; }}
QLabel#profileName {{ color: {TEXT_SOFT}; font-weight: 600; }}
QLineEdit#pinField {{ font-size: 22px; letter-spacing: 10px; padding: 10px; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#danger:hover {{ border-color: {DANGER}; }}
QTreeWidget#lockTree {{ background: {DUSK}; border: 1px solid {LINE_SOFT}; border-radius: 12px;
    padding: 6px; }}
QTreeWidget#lockTree::item {{ padding: 4px 2px; }}
QListWidget#categoryList {{ background: {DUSK}; border: 1px solid {LINE_SOFT};
    border-radius: 12px; padding: 6px; }}
QListWidget#categoryList::item {{ padding: 5px 4px; border-radius: 8px; }}
QListWidget#categoryList::item:selected {{ background: {ACCENT_TINT}; color: {TEXT}; }}
QListWidget#categoryList::indicator {{ width: 16px; height: 16px; }}
QListWidget#categoryList::indicator:unchecked {{ border: 1px solid {LINE}; border-radius: 5px;
    background: {RAISED}; }}
QListWidget#categoryList::indicator:checked {{ border: 1px solid {ACCENT}; border-radius: 5px;
    background: {ACCENT}; }}
QTreeWidget#lockTree::indicator {{ width: 16px; height: 16px; }}
QTreeWidget#lockTree::indicator:unchecked {{ border: 1px solid {LINE}; border-radius: 5px;
    background: {RAISED}; }}
QTreeWidget#lockTree::indicator:checked {{ border: 1px solid {GOLD}; border-radius: 5px;
    background: {GOLD}; }}
QLabel#accountStatus {{ color: {TEXT_SOFT}; font-weight: 600; }}
QLabel#accountStatus[state="active"] {{ color: {ACCENT}; }}
QLabel#accountStatus[state="expired"], QLabel#accountStatus[state="disabled"],
QLabel#accountStatus[state="banned"] {{ color: {DANGER}; }}

QFrame#sidebar {{ background: {DUSK}; border-right: 1px solid {LINE_SOFT}; }}
QFrame#sourceCard {{ background: {SURFACE}; border: 1px solid {LINE_SOFT}; border-radius: 14px; }}
QFrame#navIndicator {{ background: {ACCENT_TINT}; border: 1px solid #2f3b72; border-radius: 16px; }}
QFrame#library {{ background: {NIGHT}; }}
QFrame#watchPanel {{ background: {DUSK}; border-left: 1px solid {LINE_SOFT}; }}
QFrame#controls {{ background: {SURFACE}; border: 1px solid {LINE_SOFT}; border-radius: 16px; }}
QFrame#mediaInfo {{ background: {SURFACE}; border: 1px solid {LINE_SOFT}; border-radius: 14px; }}
QFrame#guide {{ background: {SURFACE}; border: 1px solid {LINE_SOFT}; border-radius: 14px; }}
QFrame#panel {{ background: {SURFACE}; border: 1px solid {LINE_SOFT}; border-radius: 16px; }}
QFrame#cardFooter {{ background: {DUSK}; border-top: 1px solid {LINE_SOFT}; }}
QWidget#cardBody {{ background: {NIGHT}; }}
QFrame#messageBar {{ background: {DUSK}; border-top: 1px solid {LINE_SOFT}; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {RAISED}; border: 1px solid {LINE}; border-radius: 10px; padding: 8px 10px;
    selection-background-color: {ACCENT_TINT}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: #34407a; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT}; background: #1e2850; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 10px;
    padding: 4px; selection-background-color: {ACCENT_TINT}; outline: none; }}

QPushButton {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 10px;
    padding: 8px 14px; }}
QPushButton:hover {{ background: {HOVER}; border-color: #3a4785; }}
QPushButton:focus {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_TINT}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; background: {DUSK}; border-color: {LINE_SOFT}; }}
QPushButton#primary {{ background: {ACCENT}; color: {ACCENT_INK}; border: 1px solid {ACCENT};
    font-weight: 600; border-radius: 12px; }}
QPushButton#primary:hover {{ background: {ACCENT_STRONG}; border-color: {ACCENT_STRONG}; }}
QPushButton#primary:pressed {{ background: #93a4f5; }}
QPushButton#nav {{ text-align: left; border: 1px solid transparent; background: transparent;
    padding: 10px 12px; color: {TEXT_SOFT}; }}
QPushButton#nav:hover {{ color: {TEXT}; background: transparent; }}
QPushButton#nav:checked {{ color: {TEXT}; font-weight: 600; background: transparent; }}
QPushButton#rail {{ border: 1px solid transparent; background: transparent; border-radius: 14px;
    color: {TEXT_SOFT}; padding: 0; }}
QPushButton#rail:hover {{ color: {TEXT}; }}
QPushButton#rail:checked {{ color: {TEXT}; font-weight: 600; }}
QPushButton#glass {{ background: rgba(232, 236, 255, 0.10); border: 1px solid rgba(232, 236, 255, 0.18);
    border-radius: 12px; padding: 8px 16px; color: {TEXT}; }}
QPushButton#glass:hover {{ background: rgba(232, 236, 255, 0.18); }}
QPushButton#ghost {{ background: transparent; border: 1px solid transparent; color: {TEXT_SOFT}; }}
QPushButton#ghost:hover {{ background: {RAISED}; border-color: {LINE}; color: {TEXT}; }}
QPushButton#ghost:disabled {{ background: transparent; border-color: transparent; }}
QPushButton#transport {{ background: transparent; border: 1px solid transparent; border-radius: 19px;
    padding: 0; }}
QPushButton#transport:hover {{ background: {RAISED}; }}
QPushButton#transport:checked {{ background: {ACCENT_TINT}; color: {ACCENT_STRONG};
    border-color: #2f3b72; }}
QPushButton#transport:disabled {{ background: transparent; border-color: transparent; }}
QPushButton#hero {{ background: {ACCENT}; border: none; border-radius: 23px; padding: 0; }}
QPushButton#hero:hover {{ background: {ACCENT_STRONG}; }}
QPushButton#hero:pressed {{ background: #93a4f5; }}
QPushButton#chip, QPushButton#chipMore {{ background: transparent; border: 1px solid {LINE};
    border-radius: 15px; padding: 0 14px; color: {TEXT_SOFT}; }}
QPushButton#chip:hover, QPushButton#chipMore:hover {{ border-color: #3a4785; color: {TEXT};
    background: transparent; }}
QPushButton#chip:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_INK};
    font-weight: 600; }}
QFrame#chipPopup {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 14px; }}
QListWidget#chipList {{ background: transparent; border: none; outline: none; }}
QListWidget#chipList::item {{ padding: 7px 10px; border-radius: 8px; }}
QListWidget#chipList::item:selected, QListWidget#chipList::item:hover {{
    background: {ACCENT_TINT}; color: {TEXT}; }}
QPushButton#rate {{ background: transparent; border: 1px solid {LINE}; border-radius: 12px;
    padding: 4px 10px; color: {TEXT_SOFT}; font-size: 9px; font-weight: 600; }}
QPushButton#rate:hover {{ border-color: {ACCENT}; color: {TEXT}; }}

QPushButton#homeScroll {{ border-radius: 15px; padding: 0; font-size: 22px; }}

QListView {{ background: transparent; border: none; outline: none; }}
QListView::item {{ border-radius: 12px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2a3463; min-height: 36px; border-radius: 3px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4785; }}
QScrollBar::handle:vertical:disabled {{ background: transparent; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2a3463; min-width: 36px; border-radius: 3px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: #27315c; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 #7f8fe6, stop:1 {ACCENT_STRONG}); border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; border: 3px solid {ACCENT}; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; border-color: {ACCENT_STRONG}; }}
QSlider::sub-page:horizontal:disabled {{ background: #27315c; }}
QSlider::handle:horizontal:disabled {{ background: #3a4470; border-color: #27315c; }}

QSplitter::handle {{ background: transparent; width: 2px; }}
QSplitter::handle:hover {{ background: {ACCENT_TINT}; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 12px; top: -1px; }}
QTabBar::tab {{ padding: 9px 16px; background: transparent; color: {TEXT_SOFT};
    border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 8px; }}
QMenu::item:selected {{ background: {ACCENT_TINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {LINE}; border-radius: 5px; background: {RAISED}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QDialogButtonBox {{ dialogbuttonbox-buttons-have-icons: 0; }}
QLabel#poster {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {RAISED}, stop:1 {DUSK});
    border: 1px solid {LINE_SOFT}; border-radius: 16px; color: {TEXT_MUTED}; }}
QToolTip {{ color: {TEXT}; background: {RAISED}; padding: 6px 9px; border: 1px solid {LINE};
    border-radius: 8px; }}
"""

STYLE += f"""
QTabBar#seasonTabs::tab {{ border-radius: 8px; margin-right: 4px; }}
QTabBar#seasonTabs::tab:selected {{ background: {ACCENT_TINT}; color: {ACCENT_STRONG}; }}
QTabBar#seasonTabs::tab:hover {{ background: {RAISED}; }}
QTabBar#seasonTabs::tab:focus {{ border-color: {ACCENT}; }}
QListView#episodeList {{ background: {SURFACE}; padding: 0; }}
"""
