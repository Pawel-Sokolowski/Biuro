from __future__ import annotations

from PyQt6.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from app.security.authorization import is_administrator
from app.utils.backup_mail import refresh_backup_log_from_mail
from app.utils.backup_status import get_backup_status
from app.db.dao_dashboard import format_duration, format_event_time, get_dashboard_data
import logging

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_MS = 60_000
BACKUP_MAIL_INTERVAL_MS = 15 * 60_000


class BackupMailWorker(QThread):
    """Pobiera log kopii z maila poza wątkiem interfejsu."""

    done = pyqtSignal()

    def run(self):
        try:
            refresh_backup_log_from_mail()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)
        self.done.emit()

CARDS = (
    ("firmy_aktywne", "Aktywne firmy"),
    ("maile_nieprzeczytane", "Nieprzeczytane e-maile"),
    ("zadania_otwarte", "Moje otwarte zadania"),
    ("zadania_zalegle", "Zadania po terminie"),
    ("sesje_dzisiaj", "Moje sesje pracy dziś"),
    ("dokumenty_miesiac", "Dokumenty w tym miesiącu"),
    ("dokumenty_dostarczone", "Dokumenty dostarczone"),
    ("dokumenty_brak", "Brak dokumentów"),
)


class DashboardPage(QWidget):
    """Strona główna: liczniki, zadania, wydarzenia i postęp bieżącego miesiąca."""

    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.cards: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel("Pulpit")
        self.title.setStyleSheet("font-size: 20px; font-weight: bold;")
        refresh = QPushButton("Odśwież")
        refresh.clicked.connect(self.refresh)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(refresh)
        layout.addLayout(header)

        grid = QGridLayout()
        layout.addLayout(grid)
        for index, (key, caption) in enumerate(CARDS):
            box = QGroupBox(caption)
            box_layout = QVBoxLayout(box)
            value = QLabel("0")
            value.setStyleSheet("font-size: 26px; font-weight: bold;")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box_layout.addWidget(value)
            self.cards[key] = value
            grid.addWidget(box, index // 4, index % 4)

        self.is_admin = is_administrator(pracownik_id)
        self.backup_box = QGroupBox("Kopie zapasowe baz")
        backup_layout = QVBoxLayout(self.backup_box)
        self.backup_label = QLabel()
        self.backup_label.setWordWrap(True)
        self.backup_list = QListWidget()
        self.backup_list.setMaximumHeight(140)
        self.backup_refresh_btn = QPushButton("Odśwież kopie z maila")
        self.backup_refresh_btn.clicked.connect(self.refresh_backup_from_mail)
        backup_layout.addWidget(self.backup_label)
        backup_layout.addWidget(self.backup_list)
        backup_layout.addWidget(self.backup_refresh_btn)
        self._backup_worker = None
        self._mail_checked_once = False
        self.backup_box.setVisible(self.is_admin)
        layout.addWidget(self.backup_box)

        self.time_label = QLabel()
        self.stages_label = QLabel()
        summary = QHBoxLayout()
        summary.addWidget(self.time_label)
        summary.addStretch()
        summary.addWidget(self.stages_label)
        layout.addLayout(summary)

        self.tasks = QListWidget()
        self.events = QListWidget()
        self.missing = QListWidget()
        lists = QHBoxLayout()
        for caption, widget in (
            ("Najbliższe zadania", self.tasks),
            ("Wydarzenia (7 dni)", self.events),
            ("Firmy bez dostarczonych dokumentów", self.missing),
        ):
            box = QGroupBox(caption)
            QVBoxLayout(box).addWidget(widget)
            lists.addWidget(box)
        layout.addLayout(lists, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_INTERVAL_MS)
        self._mail_timer = QTimer(self)
        self._mail_timer.timeout.connect(self.refresh_backup_from_mail)
        self._mail_timer.start(BACKUP_MAIL_INTERVAL_MS)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        if not self._mail_checked_once:
            self._mail_checked_once = True
            self.refresh_backup_from_mail()

    def refresh_backup_from_mail(self):
        if not self.is_admin or (self._backup_worker and self._backup_worker.isRunning()):
            return
        self.backup_refresh_btn.setEnabled(False)
        self._backup_worker = BackupMailWorker(self)
        self._backup_worker.done.connect(self._backup_mail_done)
        self._backup_worker.start()

    def _backup_mail_done(self):
        self.backup_refresh_btn.setEnabled(True)
        self._refresh_backup()

    def _refresh_backup(self):
        if not self.is_admin:
            return
        status = get_backup_status()
        colors = {"ok": "#1e8449", "nieaktualna": "#c0392b", "blad": "#c0392b"}
        self.backup_label.setText(status["komunikat"])
        self.backup_label.setStyleSheet(f"font-weight: bold; color: {colors.get(status['stan'], '#7f8c8d')};")
        self.backup_list.clear()
        for db in status["bazy"]:
            when = db["ostatnia_kopia"]
            stamp = when.strftime("%Y-%m-%d %H:%M") if when else "brak udanej kopii"
            label = db["nazwa"] + (f" ({db['firma']})" if db["firma"] and db["firma"] != db["nazwa"] else "")
            item = QListWidgetItem(f"{label} — {stamp}")
            if db["stan"] != "ok":
                item.setForeground(QColor("#c0392b"))
            self.backup_list.addItem(item)

    def refresh(self):
        data = get_dashboard_data(self.pracownik_id)
        self.title.setText(f"Pulpit — {data['miesiac']}")
        for key, label in self.cards.items():
            label.setText(str(data.get(key, 0)))
        self.cards["zadania_zalegle"].setStyleSheet(
            "font-size: 26px; font-weight: bold;"
            + (" color: #c0392b;" if data["zadania_zalegle"] else "")
        )
        self.time_label.setText(f"Mój czas pracy w tym miesiącu: {format_duration(data['sekundy_miesiac'])}")
        self.stages_label.setText(
            "Etapy miesiąca: " + ", ".join(f"{k}: {v}" for k, v in data["etapy"].items())
        )

        self._refresh_backup()

        self.tasks.clear()
        for task in data["zadania"]:
            text = f"{task['tytul']}"
            if task.get("firma"):
                text += f" [{task['firma']}]"
            if task.get("termin"):
                text += f" — termin {task['termin'][:10]}"
            self.tasks.addItem(text)
        if not data["zadania"]:
            self.tasks.addItem("Brak otwartych zadań")

        self.events.clear()
        for ev in data["wydarzenia"]:
            self.events.addItem(f"{format_event_time(ev)}  {ev['tytul']}")
        if not data["wydarzenia"]:
            self.events.addItem("Brak wydarzeń w najbliższych 7 dniach")

        self.missing.clear()
        for company in data["brak_dokumentow"]:
            self.missing.addItem(company["nazwa"] or "")
        if not data["brak_dokumentow"]:
            self.missing.addItem("Wszystkie dokumenty dostarczone")
