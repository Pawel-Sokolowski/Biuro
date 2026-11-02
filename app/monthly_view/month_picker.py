from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout,
    QWidget, QWidgetAction,
)

MONTH_LABELS = ["STY", "LUT", "MAR", "KWI", "MAJ", "CZE", "LIP", "SIE", "WRZ", "PAŹ", "LIS", "GRU"]


class MonthPicker(QToolButton):
    """Przycisk z rozwijanym małym kalendarzem miesięcy i przełączaniem roku (RRRR-MM)."""

    monthChanged = pyqtSignal(str)

    def __init__(self, month: str, parent=None, allow_year: bool = False):
        super().__init__(parent)
        self._year = int(month[:4])
        self._month = int(month[5:7])
        self._whole_year = False
        self._view_year = self._year
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._menu = QMenu(self)
        panel = QWidget(self._menu)
        layout = QVBoxLayout(panel)

        nav = QHBoxLayout()
        self._prev = QPushButton("◀")
        self._next = QPushButton("▶")
        self._year_label = QLabel()
        self._year_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._year_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        for btn in (self._prev, self._next):
            btn.setFixedWidth(32)
            btn.setFlat(True)
        nav.addWidget(self._prev)
        nav.addWidget(self._year_label, 1)
        nav.addWidget(self._next)
        layout.addLayout(nav)

        grid = QGridLayout()
        self._buttons = []
        for idx, label in enumerate(MONTH_LABELS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setMinimumWidth(52)
            btn.clicked.connect(lambda _checked=False, m=idx + 1: self._choose(m))
            grid.addWidget(btn, idx // 3, idx % 3)
            self._buttons.append(btn)
        layout.addLayout(grid)
        if allow_year:
            self._year_button = QPushButton("Cały rok")
            self._year_button.setCheckable(True)
            self._year_button.clicked.connect(self._choose_year)
            layout.addWidget(self._year_button)
        else:
            self._year_button = None

        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)
        self.setMenu(self._menu)

        self._prev.clicked.connect(lambda: self._shift_year(-1))
        self._next.clicked.connect(lambda: self._shift_year(1))
        self._menu.aboutToShow.connect(self._on_show)
        self._update_text()
        self._refresh()

    def currentText(self) -> str:
        return f"{self._year:04d}" if self._whole_year else self.monthText()

    def monthText(self) -> str:
        return f"{self._year:04d}-{self._month:02d}"

    def isWholeYear(self) -> bool:
        return self._whole_year

    def setCurrentText(self, month: str) -> None:
        self._year, self._month = int(month[:4]), int(month[5:7]) if len(month) >= 7 else self._month
        self._whole_year = len(month) < 7
        self._view_year = self._year
        self._update_text()
        self._refresh()

    def _update_text(self) -> None:
        self.setText(self.currentText())

    def _on_show(self) -> None:
        self._view_year = self._year
        self._refresh()

    def _shift_year(self, delta: int) -> None:
        self._view_year += delta
        self._refresh()

    def _refresh(self) -> None:
        self._year_label.setText(str(self._view_year))
        for idx, btn in enumerate(self._buttons):
            btn.setChecked(not self._whole_year and self._view_year == self._year and idx + 1 == self._month)
        if self._year_button is not None:
            self._year_button.setChecked(self._whole_year and self._view_year == self._year)

    def _choose_year(self) -> None:
        self._year, self._whole_year = self._view_year, True
        self._update_text()
        self._refresh()
        self._menu.close()
        self.monthChanged.emit(self.currentText())

    def _choose(self, month: int) -> None:
        self._year, self._month, self._whole_year = self._view_year, month, False
        self._update_text()
        self._refresh()
        self._menu.close()
        self.monthChanged.emit(self.currentText())
