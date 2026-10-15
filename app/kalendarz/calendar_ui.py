from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QDate, QDateTime, QLocale, QTime, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCharFormat, QFont
from PyQt6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from app.db import dao_calendar
from app.db.dao_companies import get_employees_list, list_companies
from app.kalendarz import google_sync

DT_FORMAT = "yyyy-MM-ddTHH:mm"


class _Worker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._func = func

    def run(self):
        try:
            self.finished_ok.emit(self._func())
        except Exception as exc:
            self.failed.emit(str(exc))


class EventDialog(QDialog):
    def __init__(self, pracownik_id: int, day: QDate, event: Optional[Dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wydarzenie")
        self.pracownik_id = pracownik_id
        self.event = event or {}

        self.title = QLineEdit(self.event.get("tytul", ""))
        self.all_day = QCheckBox("Cały dzień")
        self.start = QDateTimeEdit()
        self.end = QDateTimeEdit()
        for widget in (self.start, self.end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd HH:mm")
        if event:
            self.start.setDateTime(self._parse(event["start"]))
            self.end.setDateTime(self._parse(event["koniec"]))
            self.all_day.setChecked(bool(event.get("caly_dzien")))
        else:
            start = QDateTime(day, QTime(9, 0))
            self.start.setDateTime(start)
            self.end.setDateTime(start.addSecs(3600))

        self.company = QComboBox()
        self.company.addItem("— brak —", None)
        for c in list_companies(pracownik_id):
            self.company.addItem(c.get("nazwa") or "", c["id"])
        idx = self.company.findData(self.event.get("firma_id"))
        self.company.setCurrentIndex(max(idx, 0))

        self.access = QListWidget()
        self.access.setMaximumHeight(110)
        granted = set(dao_calendar.get_event_access(self.event['id'])) if self.event.get('id') else set()
        for emp in get_employees_list():
            if emp['id'] == pracownik_id:
                continue
            item = QListWidgetItem(f"{emp['imie']} {emp['nazwisko']}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if emp['id'] in granted else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, emp['id'])
            self.access.addItem(item)

        self.location = QLineEdit(self.event.get("lokalizacja") or "")
        self.description = QTextEdit(self.event.get("opis") or "")

        form = QFormLayout()
        form.addRow("Tytuł:", self.title)
        form.addRow("", self.all_day)
        form.addRow("Początek:", self.start)
        form.addRow("Koniec:", self.end)
        form.addRow("Firma:", self.company)
        form.addRow("Miejsce:", self.location)
        form.addRow("Opis:", self.description)
        form.addRow("Dostęp dla pracowników:", self.access)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    @staticmethod
    def _parse(value: str) -> QDateTime:
        return QDateTime.fromString(value if "T" in value else value + "T00:00", DT_FORMAT)

    def _accept(self):
        if not self.title.text().strip():
            QMessageBox.warning(self, "Błąd", "Podaj tytuł wydarzenia.")
            return
        if self.end.dateTime() < self.start.dateTime():
            QMessageBox.warning(self, "Błąd", "Koniec nie może być przed początkiem.")
            return
        self.accept()

    def access_ids(self) -> list[int]:
        return [
            self.access.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.access.count())
            if self.access.item(i).checkState() == Qt.CheckState.Checked
        ]

    def payload(self) -> Dict[str, Any]:
        all_day = self.all_day.isChecked()
        fmt = "yyyy-MM-dd" if all_day else DT_FORMAT
        data = {
            "id": self.event.get("id"),
            "tytul": self.title.text().strip(),
            "opis": self.description.toPlainText().strip(),
            "lokalizacja": self.location.text().strip(),
            "start": self.start.dateTime().toString(fmt),
            "koniec": self.end.dateTime().toString(fmt),
            "caly_dzien": 1 if all_day else 0,
            "firma_id": self.company.currentData(),
            "google_event_id": self.event.get("google_event_id"),
        }
        return data


class CalendarPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self._workers: list[_Worker] = []

        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale(QLocale.Language.Polish, QLocale.Country.Poland))
        self.calendar.setGridVisible(True)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.selectionChanged.connect(self._reload_day)
        self.calendar.currentPageChanged.connect(self._mark_days)

        self.day_label = QLabel()
        self.day_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        self.events = QListWidget()
        self.events.itemDoubleClicked.connect(lambda _: self._edit())

        self.btn_add = QPushButton("Dodaj")
        self.btn_edit = QPushButton("Edytuj")
        self.btn_delete = QPushButton("Usuń")
        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_delete.clicked.connect(self._delete)
        buttons = QHBoxLayout()
        for b in (self.btn_add, self.btn_edit, self.btn_delete):
            buttons.addWidget(b)

        self.google_status = QLabel()
        self.btn_google = QPushButton()
        self.btn_sync = QPushButton("Synchronizuj z Google")
        self.btn_google.clicked.connect(self._toggle_google)
        self.btn_sync.clicked.connect(self._sync)
        google_row = QHBoxLayout()
        google_row.addWidget(self.google_status)
        google_row.addStretch()
        google_row.addWidget(self.btn_google)
        google_row.addWidget(self.btn_sync)

        right = QVBoxLayout()
        right.addWidget(self.day_label)
        right.addWidget(self.events)
        right.addLayout(buttons)

        body = QHBoxLayout()
        body.addWidget(self.calendar, 3)
        body.addLayout(right, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(google_row)
        layout.addLayout(body)

        self._refresh_google_state()
        self._reload_day()
        self._mark_days()

    # ---------- dane ----------
    def _selected_day(self) -> date:
        return self.calendar.selectedDate().toPyDate()

    def _reload_day(self):
        day = self._selected_day()
        self.day_label.setText(self.calendar.locale().toString(self.calendar.selectedDate(), "dddd, dd.MM.yyyy"))
        self.events.clear()
        rows = dao_calendar.list_events(
            self.pracownik_id, day.isoformat(), (day + timedelta(days=1)).isoformat()
        )
        for ev in rows:
            when = "Cały dzień" if ev["caly_dzien"] else (
                f"{ev['start'][11:16]}–{ev['koniec'][11:16]}"
            )
            text = f"{when}  {ev['tytul']}"
            if ev.get("firma_nazwa"):
                text += f"  [{ev['firma_nazwa']}]"
            if ev.get("udostepnione"):
                text += f"  (udostępnił: {ev['wlasciciel']})"
            elif dao_calendar.get_event_access(ev['id']):
                text += "  (udostępnione)"
            if ev.get("google_event_id"):
                text += "  ☁"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ev)
            self.events.addItem(item)

    def _mark_days(self, *_):
        bold = QTextCharFormat()
        bold.setFontWeight(QFont.Weight.Bold)
        bold.setBackground(Qt.GlobalColor.yellow)
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        first = date(year, month, 1)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        for ev in dao_calendar.list_events(self.pracownik_id, first.isoformat(), nxt.isoformat()):
            d = date.fromisoformat(ev["start"][:10])
            end = date.fromisoformat(ev["koniec"][:10])
            while d <= end and d < nxt:
                if d >= first:
                    self.calendar.setDateTextFormat(QDate(d.year, d.month, d.day), bold)
                d += timedelta(days=1)

    def _current_event(self) -> Optional[Dict[str, Any]]:
        item = self.events.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ---------- CRUD ----------
    def _add(self):
        self._open_dialog(None)

    def _edit(self):
        ev = self._current_event()
        if ev and self._owned(ev):
            self._open_dialog(ev)

    def _open_dialog(self, event: Optional[Dict[str, Any]]):
        dlg = EventDialog(self.pracownik_id, self.calendar.selectedDate(), event, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.payload()
        try:
            data["id"] = dao_calendar.save_event(self.pracownik_id, data)
            dao_calendar.set_event_access(data["id"], dlg.access_ids())
        except Exception as exc:
            QMessageBox.critical(self, "Błąd", str(exc))
            return
        self._refresh_all()
        if google_sync.is_connected(self.pracownik_id):
            saved = dao_calendar.get_event(data["id"])
            self._run(lambda: google_sync.push_event(self.pracownik_id, saved), refresh=True)

    def _owned(self, ev) -> bool:
        if ev.get('udostepnione'):
            QMessageBox.information(self, 'Kalendarz', 'Wydarzenie jest udostępnione tylko do podglądu. Edytować może je wyłącznie właściciel.')
            return False
        return True

    def _delete(self):
        ev = self._current_event()
        if not ev or not self._owned(ev):
            return
        if QMessageBox.question(self, "Usuń", f"Usunąć wydarzenie „{ev['tytul']}”?") != (
            QMessageBox.StandardButton.Yes
        ):
            return
        dao_calendar.delete_event(ev["id"], self.pracownik_id)
        self._refresh_all()
        if ev.get("google_event_id") and google_sync.is_connected(self.pracownik_id):
            self._run(lambda: google_sync.delete_remote(self.pracownik_id, ev["google_event_id"]))

    def _refresh_all(self):
        self._reload_day()
        self._mark_days()

    # ---------- Google ----------
    def _refresh_google_state(self):
        connected = google_sync.is_connected(self.pracownik_id)
        self.google_status.setText(
            "Google Calendar: połączono" if connected else "Google Calendar: niepołączony"
        )
        self.btn_google.setText("Rozłącz Google" if connected else "Połącz z Google")
        self.btn_sync.setEnabled(connected)

    def _toggle_google(self):
        if google_sync.is_connected(self.pracownik_id):
            google_sync.disconnect(self.pracownik_id)
            self._refresh_google_state()
            return
        self.btn_google.setEnabled(False)
        self.google_status.setText("Czekam na zalogowanie w przeglądarce…")
        self._run(
            lambda: google_sync.connect(self.pracownik_id),
            on_done=lambda _: self._refresh_google_state(),
            on_fail=lambda: self._refresh_google_state(),
            always=lambda: self.btn_google.setEnabled(True),
        )

    def _sync(self):
        self.btn_sync.setEnabled(False)

        def done(stats):
            QMessageBox.information(
                self, "Synchronizacja",
                f"Wysłano: {stats['wyslane']}, pobrano: {stats['pobrane']}, usunięto: {stats['usuniete']}",
            )
            self._refresh_all()

        self._run(
            lambda: google_sync.sync(self.pracownik_id),
            on_done=done,
            always=lambda: self.btn_sync.setEnabled(google_sync.is_connected(self.pracownik_id)),
        )

    def _run(self, func, refresh: bool = False, on_done=None, on_fail=None, always=None):
        worker = _Worker(func, self)
        self._workers.append(worker)

        def cleanup():
            if always:
                always()
            if worker in self._workers:
                self._workers.remove(worker)

        def ok(result):
            if on_done:
                on_done(result)
            if refresh:
                self._refresh_all()
            cleanup()

        def fail(message):
            QMessageBox.warning(self, "Google Calendar", message)
            if on_fail:
                on_fail()
            cleanup()

        worker.finished_ok.connect(ok)
        worker.failed.connect(fail)
        worker.start()
