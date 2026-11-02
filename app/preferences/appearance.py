"""Local appearance preferences, isolated by signed-in employee."""
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QComboBox, QFormLayout, QLabel, QVBoxLayout, QWidget
from app.preferences.checkbox_style import CheckboxStyle

ACCENTS = [("Niebieski", "#2563eb"), ("Fioletowy", "#7c3aed"),
           ("Zielony", "#047857"), ("Turkusowy", "#0e7490"),
           ("Różowy", "#be185d"), ("Pomarańczowy", "#c2410c")]


def preferences(actor_id):
    settings = QSettings("Biuro", "OfficeManager")
    prefix = f"users/{actor_id}/appearance/"
    mode = settings.value(prefix + "mode", "light")
    accent = settings.value(prefix + "accent", ACCENTS[0][1])
    return (mode if mode in ("light", "dark") else "light",
            accent if accent in {value for _, value in ACCENTS} else ACCENTS[0][1])


def apply_appearance(actor_id):
    app = QApplication.instance()
    if app is None:
        return
    mode, accent = preferences(actor_id)
    dark = mode == "dark"
    bg, surface, raised = ("#111827", "#1f2937", "#293548") if dark else ("#f3f5f9", "#ffffff", "#e9eef6")
    text, muted, border = ("#e5eaf2", "#a8b4c5", "#3c485c") if dark else ("#1e293b", "#64748b", "#d5deea")
    app.setStyle(CheckboxStyle())
    palette = QPalette()
    for role, colour in ((QPalette.ColorRole.Window, bg), (QPalette.ColorRole.WindowText, text),
                         (QPalette.ColorRole.Base, surface), (QPalette.ColorRole.AlternateBase, raised),
                         (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, surface),
                         (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.Highlight, accent),
                         (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.Link, accent),
                         (QPalette.ColorRole.ToolTipBase, surface), (QPalette.ColorRole.ToolTipText, text),
                         (QPalette.ColorRole.PlaceholderText, muted)):
        palette.setColor(role, QColor(colour))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(muted))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QWidget {{ color: {text}; font-family: 'Segoe UI', Arial; font-size: 10pt; }}
        QMainWindow, QDialog, QScrollArea, QStackedWidget {{ background: {bg}; }}
        QLabel {{ background: transparent; }}
        QPushButton {{ background: {accent}; color: white; padding: 8px 14px;
            border: 1px solid transparent; border-radius: 6px; font-weight: 600; }}
        QPushButton:hover {{ background: {QColor(accent).darker(115).name()}; }}
        QPushButton:pressed {{ background: {QColor(accent).darker(130).name()}; }}
        QPushButton:disabled {{ background: {raised}; color: {muted}; border-color: {border}; }}
        QPushButton:focus {{ border: 1px solid {text}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QComboBox {{
            background: {surface}; color: {text}; border: 1px solid {border};
            border-radius: 5px; padding: 6px; min-height: 20px; selection-background-color: {accent}; selection-color: white; }}
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
        QTableView, QTreeView, QListView {{ background: {surface}; alternate-background-color: {raised};
            color: {text}; border: 1px solid {border}; border-radius: 6px;
            gridline-color: {border}; selection-background-color: {accent}; selection-color: white;
            show-decoration-selected: 1; }}
        QTableView::item:hover, QTreeView::item:hover, QListView::item:hover {{ background: {raised}; }}
        QTableView::item:selected, QTreeView::item:selected, QListView::item:selected,
        QTableView::item:selected:!active, QTreeView::item:selected:!active, QListView::item:selected:!active {{
            background: {accent}; color: white; }}
        QHeaderView::section {{ background: {raised}; color: {muted}; padding: 9px;
            border: none; border-bottom: 1px solid {border}; font-weight: 600; }}
        QTableCornerButton::section {{ background: {raised}; border: none; }}
        QListView::item {{ padding: 7px 9px; }}
        QTabWidget::pane {{ border: 1px solid {border}; background: {surface}; border-radius: 6px; }}
        QTabBar::tab {{ background: {bg}; color: {muted}; padding: 10px 18px;
            border-bottom: 3px solid transparent; }}
        QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; background: {surface}; }}
        QGroupBox {{ border: 1px solid {border}; border-radius: 8px; margin-top: 14px; padding: 14px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
        QMenu, QComboBox QAbstractItemView {{ background: {surface}; color: {text}; border: 1px solid {border}; }}
        QMenu::item:selected {{ background: {accent}; color: white; }}
        QToolTip {{ background: {surface}; color: {text}; border: 1px solid {border}; padding: 5px; }}
        QListWidget#mainSidebar {{ background: {surface}; border: none; border-radius: 0; font-size: 11pt; }}
        QListWidget#mainSidebar::item {{ padding: 13px 16px; margin: 3px 8px; border-radius: 6px; }}
        QListWidget#mainSidebar::item:hover {{ background: {raised}; }}
        QListWidget#mainSidebar::item:selected {{ background: {accent}; color: white; font-weight: 600; }}
        _DayCell {{ background: {surface}; border: 1px solid {border}; border-radius: 5px; }}
        _DayCell[dimmed="true"] {{ background: {bg}; }}
        _DayCell[today="true"] {{ background: {raised}; border: 2px solid {accent}; }}
        _DayCell[selectedDay="true"] {{ border: 3px solid {accent}; }}
        QWidget#appHeader {{ background: {surface}; border-bottom: 1px solid {border}; }}
    """)


class AppearanceSettings(QWidget):
    def __init__(self, actor_id, parent=None):
        super().__init__(parent)
        self.actor_id = actor_id
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        title = QLabel("Wygląd aplikacji")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        description = QLabel("Dostosuj aplikację do siebie. Zmiany są zapisywane automatycznie dla Twojego konta na tym komputerze.")
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        form.setSpacing(12)
        self.mode = QComboBox()
        self.mode.addItem("Jasny", "light")
        self.mode.addItem("Ciemny", "dark")
        self.accent = QComboBox()
        for name, value in ACCENTS:
            self.accent.addItem(name, value)
            self.accent.setItemData(self.accent.count() - 1, QColor(value), Qt.ItemDataRole.DecorationRole)
        mode, accent = preferences(actor_id)
        self.mode.setCurrentIndex(self.mode.findData(mode))
        self.accent.setCurrentIndex(self.accent.findData(accent))
        form.addRow("Motyw:", self.mode)
        form.addRow("Kolor akcentu:", self.accent)
        layout.addLayout(form)
        layout.addStretch()
        self.mode.currentIndexChanged.connect(self._save)
        self.accent.currentIndexChanged.connect(self._save)

    def _save(self):
        settings = QSettings("Biuro", "OfficeManager")
        prefix = f"users/{self.actor_id}/appearance/"
        settings.setValue(prefix + "mode", self.mode.currentData())
        settings.setValue(prefix + "accent", self.accent.currentData())
        settings.sync()
        apply_appearance(self.actor_id)
