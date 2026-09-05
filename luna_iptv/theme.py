"""Original Luna desktop tokens, based on the user's color and type brief."""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

SURFACE = "#1d2021"
ACCENT = "#e889a8"
TEXT = "#f8e7ec"


def apply_theme(app):
    app.setStyle("Fusion")
    family = "Hurmit Nerd Font Propo"
    if family not in QFontDatabase.families():
        family = "Sans Serif"
    app.setFont(QFont(family, 10))
    palette = QPalette()
    for role, color in [
        (QPalette.Window, SURFACE),
        (QPalette.WindowText, TEXT),
        (QPalette.Base, "#191c1d"),
        (QPalette.AlternateBase, "#252829"),
        (QPalette.Text, TEXT),
        (QPalette.Button, "#292c2d"),
        (QPalette.ButtonText, TEXT),
        (QPalette.Highlight, "#583f49"),
        (QPalette.HighlightedText, TEXT),
        (QPalette.ToolTipBase, "#303334"),
        (QPalette.ToolTipText, TEXT),
        (QPalette.PlaceholderText, "#ada5a8"),
    ]:
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#817b7e"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#817b7e"))
    app.setPalette(palette)
    app.setStyleSheet("""
        QMainWindow, QDialog { background: #1d2021; }
        QWidget { color: #f8e7ec; }
        QLabel { background: transparent; }
        QLabel#brand { font-size: 21px; font-weight: 700; letter-spacing: 3px; }
        QLabel#eyebrow { color: #b2aaad; font-size: 10px; letter-spacing: 2px; }
        QLabel#heading { font-size: 23px; font-weight: 600; }
        QLabel#muted { color: #b2aaad; }
        QLabel#badge { color: #e889a8; font-size: 10px; }
        QFrame#sidebar { background: #181b1c; border-right: 1px solid #343637; }
        QFrame#library { border-right: 1px solid #343637; }
        QFrame#controls { background: #242728; border-radius: 8px; }
        QFrame#guide { border-top: 1px solid #393b3c; }
        QLineEdit, QComboBox { background: #242728; border: 1px solid #48494a; border-radius: 6px; padding: 9px; selection-background-color: #583f49; }
        QLineEdit:focus, QComboBox:focus { border-color: #e889a8; }
        QPushButton { background: #292c2d; border: 1px solid #494749; border-radius: 6px; padding: 9px 13px; }
        QPushButton:hover { background: #363334; border-color: #b3778d; }
        QPushButton:focus { border-color: #e889a8; }
        QPushButton:pressed { background: #49343d; }
        QPushButton:disabled { color: #8b8386; border-color: #343637; }
        QPushButton#primary { background: #e889a8; color: #201a1d; border: 1px solid #e889a8; font-weight: 600; }
        QPushButton#primary:hover { background: #efa0b9; }
        QPushButton#nav { text-align: left; border: 1px solid transparent; background: transparent; padding: 12px; }
        QPushButton#nav:checked { background: #3a2e34; color: #f2adc5; border-color: #624451; }
        QPushButton#nav:hover { background: #2c282b; }
        QListView { background: transparent; border: none; outline: none; }
        QListView::item { border-radius: 6px; }
        QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
        QScrollBar::handle:vertical { background: #5d555a; min-height: 30px; border-radius: 3px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QSlider::groove:horizontal { height: 4px; background: #51474c; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: #e889a8; border-radius: 2px; }
        QSlider::handle:horizontal { background: #f8e7ec; width: 12px; margin: -4px 0; border-radius: 6px; }
        QTabWidget::pane { border: 1px solid #48494a; border-radius: 6px; }
        QTabBar::tab { padding: 10px 14px; background: #242728; }
        QTabBar::tab:selected { color: #e889a8; border-bottom: 2px solid #e889a8; }
        QToolTip { padding: 5px; border: 1px solid #66515b; }
    """)
