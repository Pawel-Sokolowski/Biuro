from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from PyQt6.QtCore import QDate, QDateTime, QLocale, QTime, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QFont
from PyQt6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QFileDialog, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.db import dao_calendar
from app.db.dao_companies import get_employees_list, list_companies
from app.kalendarz import caldav_sync, ics_import

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
            self.failed.emit(caldav_sync.friendly_error(exc))


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
        if self.access.count() == 0:
            hint = QListWidgetItem('Brak innych pracowników - dodaj ich w Ustawienia -> Użytkownicy')
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.access.addItem(hint)

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
            if self.access.item(i).data(Qt.ItemDataRole.UserRole) is not None
            and self.access.item(i).checkState() == Qt.CheckState.Checked
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
            "caldav_uid": self.event.get("caldav_uid"),
            "zrodlo": self.event.get("zrodlo") or "lokalne",
        }
        return data


MONTHS_PL = (
    "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
    "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
)
WEEKDAYS_PL = ("pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz.")
VIEWS = ("Dzień", "Tydzień", "Wiele tygodni", "Miesiąc")
SOURCE_COLORS = {"lokalne": "#1976d2", "caldav": "#2e7d32"}
MAX_CHIPS = 3


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class _Chip(QLabel):
    clicked = pyqtSignal(object)
    double_clicked = pyqtSignal(object)

    def __init__(self, ev: Dict[str, Any], parent=None):
        when = "" if ev["caly_dzien"] else ev["start"][11:16] + " "
        super().__init__(f"{when}{ev['tytul']}", parent)
        self.ev = ev
        color = SOURCE_COLORS.get(ev.get("zrodlo") or "lokalne", SOURCE_COLORS["lokalne"])
        if ev.get("udostepnione"):
            color = "#8e24aa"
        self.setStyleSheet(
            f"background:{color}; color:white; border-radius:3px; padding:1px 4px; font-size:11px;"
        )
        self.setToolTip(f"{ev['tytul']}\n{ev['start'].replace('T', ' ')} – {ev['koniec'].replace('T', ' ')}")

    def mousePressEvent(self, e):
        self.clicked.emit(self.ev)

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit(self.ev)


class _DayCell(QFrame):
    day_clicked = pyqtSignal(object)
    day_double_clicked = pyqtSignal(object)
    event_clicked = pyqtSignal(object)
    event_double_clicked = pyqtSignal(object)

    def __init__(self, day: date, events: list, dimmed: bool, today: bool, parent=None):
        super().__init__(parent)
        self.day = day
        self.setFrameShape(QFrame.Shape.StyledPanel)
        bg = "#fff4c2" if today else ("#f3f3f3" if dimmed else "white")
        self.setStyleSheet(f"_DayCell {{ background:{bg}; border:1px solid #d0d0d0; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(1)
        num = QLabel(str(day.day) if day.day != 1 else f"1 {MONTHS_PL[day.month - 1][:3]}")
        num.setStyleSheet(
            f"font-weight:{'bold' if today else 'normal'}; color:{'#999' if dimmed else '#222'};"
        )
        num.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(num)
        for ev in events[:MAX_CHIPS]:
            chip = _Chip(ev)
            chip.clicked.connect(self.event_clicked)
            chip.double_clicked.connect(self.event_double_clicked)
            lay.addWidget(chip)
        if len(events) > MAX_CHIPS:
            lay.addWidget(QLabel(f"+{len(events) - MAX_CHIPS} więcej"))
        lay.addStretch()

    def mousePressEvent(self, e):
        self.day_clicked.emit(self.day)

    def mouseDoubleClickEvent(self, e):
        self.day_double_clicked.emit(self.day)


class CalendarPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self._workers: list[_Worker] = []
        self.anchor = date.today()

        # --- pasek narzędzi ---
        self.btn_sync = QPushButton("Synchronizuj")
        self.btn_import = QPushButton("Importuj .ics")
        self.btn_event = QPushButton("Wydarzenie")
        self.btn_task = QPushButton("Zadanie")
        self.btn_edit = QPushButton("Edytuj")
        self.btn_delete = QPushButton("Usuń")
        self.btn_new = QPushButton("Nowe wydarzenie")
        self.btn_new.setStyleSheet("background:#1976d2; color:white; padding:4px 12px; font-weight:bold;")
        self.btn_sync.clicked.connect(self._sync)
        self.btn_import.clicked.connect(self._import_ics)
        self.btn_event.clicked.connect(self._add)
        self.btn_new.clicked.connect(self._add)
        self.btn_task.clicked.connect(self._add_task)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_delete.clicked.connect(self._delete)
        toolbar = QHBoxLayout()
        for b in (self.btn_sync, self.btn_import, self.btn_event, self.btn_task, self.btn_edit, self.btn_delete):
            toolbar.addWidget(b)
        toolbar.addStretch()
        toolbar.addWidget(self.btn_new)

        # --- panel boczny ---
        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale(QLocale.Language.Polish, QLocale.Country.Poland))
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.selectionChanged.connect(self._mini_selected)
        self.calendar.currentPageChanged.connect(self._mark_days)

        self.calendars = QListWidget()
        self.calendars.itemChanged.connect(lambda _: self._rebuild())
        self.status = QLabel("")
        self.status.setWordWrap(True)
        side = QVBoxLayout()
        side.addWidget(self.calendar)
        side.addWidget(QLabel("<b>Kalendarze</b>"))
        side.addWidget(self.calendars, 1)
        side.addWidget(self.status)
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setMaximumWidth(300)

        # --- nawigacja i widoki ---
        self.view_buttons: list[QPushButton] = []
        nav = QHBoxLayout()
        for i, name in enumerate(VIEWS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, idx=i: self._set_view(idx))
            self.view_buttons.append(b)
            nav.addWidget(b)
        self.view = 3
        self.view_buttons[3].setChecked(True)
        nav.addStretch()
        self.btn_prev = QPushButton("◀")
        self.btn_today = QPushButton("Dzisiaj")
        self.btn_next = QPushButton("▶")
        self.btn_prev.clicked.connect(lambda: self._shift(-1))
        self.btn_next.clicked.connect(lambda: self._shift(1))
        self.btn_today.clicked.connect(self._go_today)
        self.period_label = QLabel()
        self.period_label.setStyleSheet("font-weight:bold; font-size:15px; padding:0 8px;")
        for w in (self.btn_prev, self.btn_today, self.btn_next, self.period_label):
            nav.addWidget(w)

        # --- lista wydarzeń + szukanie ---
        self.search = QLineEdit()
        self.search.setPlaceholderText("zawiera:")
        self.search.textChanged.connect(lambda _: self._rebuild())
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("zawiera:"))
        search_row.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Nazwa", "Początek", "Koniec", "Kategoria"])
        self.tree.setRootIsDecorated(False)
        self.tree.setMaximumHeight(170)
        self.tree.itemDoubleClicked.connect(lambda *_: self._edit())
        self.events = self.tree

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(0)
        self.grid.setContentsMargins(0, 0, 0, 0)

        main = self._main = QVBoxLayout()
        main.addLayout(nav)
        main.addLayout(search_row)
        main.addWidget(self.tree)
        main.addWidget(self.grid_host, 1)

        body = QHBoxLayout()
        body.addWidget(side_w)
        body.addLayout(main, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addLayout(body)

        self._fill_calendar_list()
        self._rebuild()

    # ---------- kalendarze ----------
    def _fill_calendar_list(self):
        self.calendars.blockSignals(True)
        self.calendars.clear()
        entries = [("Lokalne (Biuro)", "lokalne")]
        if caldav_sync.is_configured():
            host = urlparse(caldav_sync.get_config()["url"]).hostname or "CalDAV"
            entries.append((f"CalDAV: {host}", "caldav"))
        for name, key in entries:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setForeground(QColor(SOURCE_COLORS[key]))
            self.calendars.addItem(item)
        self.calendars.blockSignals(False)

    def _enabled_sources(self) -> set:
        return {
            self.calendars.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.calendars.count())
            if self.calendars.item(i).checkState() == Qt.CheckState.Checked
        }

    # ---------- nawigacja ----------
    def _selected_day(self) -> date:
        return self.anchor

    def _set_view(self, idx: int):
        self.view = idx
        for i, b in enumerate(self.view_buttons):
            b.setChecked(i == idx)
        self._rebuild()

    def _shift(self, direction: int):
        a = self.anchor
        if self.view == 0:
            a += timedelta(days=direction)
        elif self.view == 1:
            a += timedelta(weeks=direction)
        elif self.view == 2:
            a += timedelta(weeks=4 * direction)
        else:
            m = a.month - 1 + direction
            y, m = a.year + m // 12, m % 12 + 1
            a = date(y, m, min(a.day, calendar.monthrange(y, m)[1]))
        self._goto(a)

    def _go_today(self):
        self._goto(date.today())

    def _goto(self, d: date):
        self.anchor = d
        self.calendar.blockSignals(True)
        self.calendar.setSelectedDate(QDate(d.year, d.month, d.day))
        self.calendar.blockSignals(False)
        self._rebuild()

    def _mini_selected(self):
        self._goto(self.calendar.selectedDate().toPyDate())

    def _range(self):
        """(pierwszy dzień, dzień po ostatnim, liczba tygodni w siatce)."""
        a = self.anchor
        if self.view == 0:
            return a, a + timedelta(days=1), 0
        if self.view == 1:
            m = _monday(a)
            return m, m + timedelta(days=7), 1
        if self.view == 2:
            m = _monday(a)
            return m, m + timedelta(days=28), 4
        first = date(a.year, a.month, 1)
        last = date(a.year, a.month, calendar.monthrange(a.year, a.month)[1])
        m = _monday(first)
        weeks = ((_monday(last) - m).days // 7) + 1
        return m, m + timedelta(days=7 * weeks), weeks

    # ---------- dane ----------
    def _visible_events(self, first: date, last: date) -> list:
        sources = self._enabled_sources()
        needle = self.search.text().strip().lower()
        rows = dao_calendar.list_events(self.pracownik_id, first.isoformat(), last.isoformat())
        out = []
        for ev in rows:
            source = ev.get("zrodlo") or "lokalne"
            if source not in SOURCE_COLORS:
                source = "lokalne"  # np. zaimportowane z .ics
            if source not in sources:
                continue
            hay = f"{ev['tytul']} {ev.get('opis') or ''} {ev.get('lokalizacja') or ''} {ev.get('firma_nazwa') or ''}"
            if needle and needle not in hay.lower():
                continue
            out.append(ev)
        return out

    def _rebuild(self):
        first, last, weeks = self._range()
        a = self.anchor
        if self.view == 0:
            self.period_label.setText(f"{a.day} {MONTHS_PL[a.month - 1]} {a.year}")
        elif self.view == 3:
            self.period_label.setText(f"{MONTHS_PL[a.month - 1]} {a.year}")
        else:
            end = last - timedelta(days=1)
            self.period_label.setText(f"{first.strftime('%d.%m')} – {end.strftime('%d.%m.%Y')}")
        events = self._visible_events(first, last)
        self._fill_tree(events)
        self.grid_host.setVisible(weeks > 0)
        self.tree.setMaximumHeight(170 if weeks else 16777215)
        self._main.setStretchFactor(self.tree, 0 if weeks else 1)
        if weeks:
            self._fill_grid(first, weeks, events)
        self._mark_days()

    def _fill_tree(self, events: list):
        self.tree.clear()
        for ev in events:
            if ev["caly_dzien"]:
                s, e = ev["start"][:10], ev["koniec"][:10]
            else:
                s, e = ev["start"].replace("T", " "), ev["koniec"].replace("T", " ")
            if ev.get("firma_nazwa"):
                cat = ev["firma_nazwa"]
            else:
                cat = "CalDAV" if ev.get("zrodlo") == "caldav" else "Lokalne"
            title = ev["tytul"]
            if ev.get("udostepnione"):
                title += f"  (udostępnił: {ev['wlasciciel']})"
            elif dao_calendar.get_event_access(ev["id"]):
                title += "  (udostępnione)"
            item = QTreeWidgetItem([title, s, e, cat])
            item.setData(0, Qt.ItemDataRole.UserRole, ev)
            self.tree.addTopLevelItem(item)
        for c in range(4):
            self.tree.resizeColumnToContents(c)

    def _fill_grid(self, first: date, weeks: int, events: list):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        by_day: Dict[date, list] = {}
        for ev in events:
            d = date.fromisoformat(ev["start"][:10])
            end = date.fromisoformat(ev["koniec"][:10])
            if not ev["caly_dzien"] and ev["koniec"][11:16] == "00:00" and end > d:
                end -= timedelta(days=1)
            while d <= end:
                by_day.setdefault(d, []).append(ev)
                d += timedelta(days=1)
        corner = QLabel("")
        self.grid.addWidget(corner, 0, 0)
        for c, name in enumerate(WEEKDAYS_PL):
            h = QLabel(name)
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.setStyleSheet("font-weight:bold; background:#eceff1; padding:2px;")
            self.grid.addWidget(h, 0, c + 1)
        today = date.today()
        for r in range(weeks):
            monday = first + timedelta(weeks=r)
            wk = QLabel(f"{monday.isocalendar()[1]}. tydz.")
            wk.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            wk.setStyleSheet("color:#666; background:#eceff1; padding:2px; font-size:11px;")
            wk.setFixedWidth(62)
            self.grid.addWidget(wk, r + 1, 0)
            for c in range(7):
                d = monday + timedelta(days=c)
                dimmed = self.view == 3 and d.month != self.anchor.month
                cell = _DayCell(d, by_day.get(d, []), dimmed, d == today)
                cell.day_clicked.connect(self._cell_clicked)
                cell.day_double_clicked.connect(self._cell_double_clicked)
                cell.event_clicked.connect(self._select_event)
                cell.event_double_clicked.connect(lambda ev: (self._select_event(ev), self._edit()))
                self.grid.addWidget(cell, r + 1, c + 1)
            self.grid.setRowStretch(r + 1, 1)
        for c in range(1, 8):
            self.grid.setColumnStretch(c, 1)

    def _cell_clicked(self, d: date):
        self.anchor = d
        self.calendar.blockSignals(True)
        self.calendar.setSelectedDate(QDate(d.year, d.month, d.day))
        self.calendar.blockSignals(False)

    def _cell_double_clicked(self, d: date):
        self._cell_clicked(d)
        self._add()

    def _select_event(self, ev: Dict[str, Any]):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole)["id"] == ev["id"]:
                self.tree.setCurrentItem(item)
                return

    def _mark_days(self, *_):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold)
        fmt.setBackground(QColor("#bbdefb"))
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        year, month = self.calendar.yearShown(), self.calendar.monthShown()
        first = date(year, month, 1)
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        for ev in dao_calendar.list_events(self.pracownik_id, first.isoformat(), nxt.isoformat()):
            d = date.fromisoformat(ev["start"][:10])
            end = date.fromisoformat(ev["koniec"][:10])
            while d <= end and d < nxt:
                if d >= first:
                    self.calendar.setDateTextFormat(QDate(d.year, d.month, d.day), fmt)
                d += timedelta(days=1)

    def _current_event(self) -> Optional[Dict[str, Any]]:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    # ---------- CRUD ----------
    def _add(self):
        self._open_dialog(None)

    def _add_task(self):
        self._open_dialog(None, task=True)

    def _edit(self):
        ev = self._current_event()
        if ev and self._owned(ev):
            self._open_dialog(ev)

    def _open_dialog(self, event: Optional[Dict[str, Any]], task: bool = False):
        d = self.anchor
        dlg = EventDialog(self.pracownik_id, QDate(d.year, d.month, d.day), event, self)
        if task:
            dlg.title.setText("Zadanie: ")
            dlg.all_day.setChecked(True)
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
        if caldav_sync.is_configured() and data.get("zrodlo") != "google":
            saved = dao_calendar.get_event(data["id"])
            self._run(lambda: caldav_sync.push_event(self.pracownik_id, saved), refresh=True)

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
        if ev.get("caldav_uid") and caldav_sync.is_configured():
            self._run(lambda: caldav_sync.delete_remote(self.pracownik_id, ev["caldav_uid"]))

    def _refresh_all(self):
        self._fill_calendar_list()
        self._rebuild()

    # ---------- import .ics ----------
    def _import_ics(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importuj kalendarz", "", "Plik programu iCalendar (*.ics);;Wszystkie pliki (*)"
        )
        if not path:
            return
        try:
            stats = ics_import.import_ics(self.pracownik_id, path)
        except Exception as exc:
            QMessageBox.warning(self, "Import kalendarza", f"Nie udało się wczytać pliku: {exc}")
            return
        self.status.setText(
            f"Zaimportowano: {stats['zaimportowane']}, pominięto duplikaty: {stats['pominiete']}"
        )
        self._refresh_all()

    # ---------- synchronizacja CalDAV ----------
    def _sync(self):
        if not caldav_sync.is_configured():
            QMessageBox.information(
                self, "Synchronizacja",
                "Najpierw skonfiguruj połączenie CalDAV w Ustawieniach → Kalendarz.",
            )
            return
        self.btn_sync.setEnabled(False)
        self.status.setText("Synchronizacja…")

        def done(stats):
            self.status.setText(
                f"Wysłano: {stats['wyslane']}, pobrano: {stats['pobrane']}, usunięto: {stats['usuniete']}"
            )
            self._refresh_all()

        self._run(
            lambda: caldav_sync.sync(self.pracownik_id),
            on_done=done,
            on_fail=lambda: self.status.setText("Synchronizacja nie powiodła się."),
            always=lambda: self.btn_sync.setEnabled(True),
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
            QMessageBox.warning(self, "Kalendarz", message)
            if on_fail:
                on_fail()
            cleanup()

        worker.finished_ok.connect(ok)
        worker.failed.connect(fail)
        worker.start()
