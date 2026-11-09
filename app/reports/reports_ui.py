# app/reports/reports_ui.py
from app.utils.ui_imports import *
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QCheckBox, QCalendarWidget, QFileDialog
from PyQt6.QtCharts import (
    QChart, QChartView,
    QBarSeries, QBarSet,
    QLineSeries,
    QValueAxis, QBarCategoryAxis
)
from PyQt6.QtGui import QPainter
import datetime
import logging
from app.utils.background import TaskRunner, CommandRunner
from app.utils.item_table import ItemTable
from app.utils.table_records import TableRecord

from app.reports.reports_logic import (
    get_all_companies,
    get_months_range,
    build_report,
    build_daily_report,
)
from app.reports.import_parse import SEKCJE, SEKCJA_LABELS

PROFIT_MODE = "Rentowność"
DAILY_MODE = "Raport dzienny"
DAILY_HEADERS = ["Data", "Pracownik", "Firma", "Miesiąc rozliczeniowy", "Zadanie / czynność",
                 "Śr. dokumentów / mies.", "Umowy (miesiąc)", "Czas"]


def iter_daily_display_rows(rows):
    """Data i pracownik tylko przy pierwszym wystąpieniu; pusty wiersz między dniami."""
    last_day, last_emp = None, None
    for item in rows:
        if last_day is not None and item["data"] != last_day:
            yield [""] * len(DAILY_HEADERS)
            last_emp = None
        new_day = item["data"] != last_day
        yield [
            datetime.date.fromisoformat(item["data"]).strftime("%d/%m/%Y") if new_day else "",
            item["pracownik"] if new_day or item["pracownik"] != last_emp else "",
            item.get("firma") or "", item.get('miesiac') or '', item["czynnosc"],
            (f"{item['dokumenty_srednia']:.2f}" if item.get('dokumenty_srednia') is not None
             else '—' if item.get('firma') else ''),
            str(item['umowy']) if item.get('umowy') is not None else '', format_hours(item["sekundy"]),
        ]
        last_day, last_emp = item["data"], item["pracownik"]



def daily_display_rows(rows):
    return list(iter_daily_display_rows(rows))


def daily_range(day: datetime.date, whole_month: bool) -> tuple[str, str]:
    """Zakres dat (włącznie) dla raportu dziennego: wybrany dzień albo cały miesiąc."""
    if not whole_month:
        return day.isoformat(), day.isoformat()
    first = day.replace(day=1)
    last = (first.replace(year=first.year + (first.month == 12), month=first.month % 12 + 1)
            - datetime.timedelta(days=1))
    return first.isoformat(), last.isoformat()
PROFIT_HEADERS = {
    "ksiegowosc": ["Firma", "Miesiąc", "Pracownik", "Ilość dokumentów", "Czas pracy księgowość",
                   "Księgowość", "Czas pracy usługi dodatkowe", "Usługi dodatkowe", "Koszt pracownika", "Zysk / strata"],
    "kadry": ["Firma", "Miesiąc", "Pracownik", "Ilość umów", "Czas pracy kadry",
              "Kadry", "Czas pracy usługi dodatkowe", "Usługi dodatkowe", "Koszt pracownika", "Zysk / strata"],
}
REPORT_HEADERS = ["Klient", "Miesiąc", "Ilość dokumentów", "Czas księgowości", "Ilość umów", "Czas kadr"]


def format_hours(seconds: float) -> str:
    """Konwersja sekund → H:MM"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}:{m:02d}"


def fit_table_height(table) -> None:
    """Tabela dostaje wysokość dokładnie na wszystkie wiersze (bez własnego przewijania)."""
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    height = table.horizontalHeader().height() + 2 * table.frameWidth() + 4
    for row in range(table.rowCount()):
        height += table.rowHeight(row)
    table.setFixedHeight(max(height, table.horizontalHeader().height() + 40))


class ReportsPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id
        self._report_jobs = TaskRunner(self)
        self._import_jobs = CommandRunner(self)
        self._export_jobs = CommandRunner(self)
        self._import_busy = False
        self._build_ui()
        for signal in (self.mode_combo.currentTextChanged, self.month_start.currentTextChanged,
                       self.month_end.currentTextChanged, self.year_combo.currentTextChanged,
                       self.firm_list.itemSelectionChanged, self.employee_list.itemSelectionChanged,
                       self.daily_from.selectionChanged, self.daily_to.selectionChanged,
                       self.chk_whole_month.toggled):
            signal.connect(lambda *_: self._report_jobs.invalidate())

    def _build_ui(self):
        from PyQt6.QtWidgets import QScrollArea

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        # sterowanie (rodzaj raportu, firmy, zakres, przyciski) jest stałe - przewijają się tylko wyniki
        layout = outer

        title = QLabel("<b>Raporty</b>")
        title.setFont(QFont("Arial", 18))
        layout.addWidget(title)

        mode_box = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "Raport miesięczny",
            "Raport roczny",
            PROFIT_MODE,
            DAILY_MODE,
        ])
        mode_box.addWidget(QLabel("Tryb:"))
        mode_box.addWidget(self.mode_combo)
        layout.addLayout(mode_box)

        self.firm_list = QListWidget()
        self.firm_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.company_names = {}
        for f in get_all_companies(self.pracownik_id):
            self.company_names[f["id"]] = f["nazwa"]
            item = QListWidgetItem(f"{f['nazwa']} (ID {f['id']})")
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self.firm_list.addItem(item)
        layout.addWidget(self.firm_list)

        self.month_start = QComboBox()
        self.month_end = QComboBox()
        self.year_combo = QComboBox()

        now = datetime.datetime.now()
        first_year = 2026
        months = [f"{y}-{m:02d}" for y in range(first_year, max(now.year, first_year) + 1) for m in range(1, 13)]

        self.month_start.addItems(months)
        self.month_end.addItems(months)
        # domyślnie cały bieżący rok (styczeń–grudzień); zakres można skrócić
        default_year = max(now.year, first_year)
        self.month_start.setCurrentText(f"{default_year}-01")
        self.month_end.setCurrentText(f"{default_year}-12")
        self.year_combo.addItems([str(y) for y in range(first_year, max(now.year, first_year) + 1)])

        self.month_box_container = QWidget()
        self.month_box = QHBoxLayout(self.month_box_container)
        self.month_box.addWidget(QLabel("Od:"))
        self.month_box.addWidget(self.month_start)
        self.month_box.addWidget(QLabel("Do:"))
        self.month_box.addWidget(self.month_end)
        layout.addWidget(self.month_box_container)

        self.year_box_container = QWidget()
        self.year_box = QHBoxLayout(self.year_box_container)
        self.year_box.addWidget(QLabel("Rok:"))
        self.year_box.addWidget(self.year_combo)
        layout.addWidget(self.year_box_container)

        self.daily_box_container = QWidget()
        daily_box = QHBoxLayout(self.daily_box_container)
        self.employee_list = QListWidget()
        self.employee_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.employee_list.setMaximumHeight(200)
        for emp_id, label in self._reportable_employees():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, emp_id)
            self.employee_list.addItem(item)
        daily_box.addWidget(self.employee_list, 1)
        self.daily_from = QCalendarWidget()
        self.daily_to = QCalendarWidget()
        self.daily_from.setMaximumHeight(200)
        self.daily_to.setMaximumHeight(200)
        self.chk_whole_month = QCheckBox("Cały miesiąc (z daty „Od”)")
        for title, calendar in (("Od dnia", self.daily_from), ("Do dnia", self.daily_to)):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(calendar)
            daily_box.addLayout(column, 1)
        daily_box.addWidget(self.chk_whole_month)
        self.daily_from.selectionChanged.connect(self._daily_from_changed)
        self.chk_whole_month.toggled.connect(self.daily_to.setDisabled)
        layout.addWidget(self.daily_box_container)

        self.mode_combo.currentTextChanged.connect(self._update_mode_ui)

        btn = QPushButton("Generuj raport")
        btn.setProperty("class", "primary")
        btn.clicked.connect(self._generate_report_async)
        layout.addWidget(btn)

        btn_export = QPushButton("Eksport do Excel")
        btn_export.clicked.connect(lambda: self._export_to_excel(background=True))
        layout.addWidget(btn_export)

        import_box = QHBoxLayout()
        btn_import_work = QPushButton("Importuj czas pracy (Excel)")
        btn_import_work.clicked.connect(self._import_work)
        btn_import_inv = QPushButton("Importuj faktury (Excel)")
        btn_import_inv.clicked.connect(self._import_invoices)
        btn_rates = QPushButton("Stawki pracowników")
        btn_rates.clicked.connect(self._edit_rates)
        btn_delete = QPushButton("Usuń zaimportowane dane")
        btn_delete.clicked.connect(self._delete_imported)
        for b in (btn_import_work, btn_import_inv, btn_rates, btn_delete):
            import_box.addWidget(b)
        layout.addLayout(import_box)

        self.chk_overwrite = QCheckBox("Nadpisz istniejące dane (ilość dokumentów w widoku miesięcznym)")
        layout.addWidget(self.chk_overwrite)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)

        self.common_label = QLabel("<b>Wspólny</b>")
        layout.addWidget(self.common_label)
        self.common_table = self._make_table(["Dział", "Fv suma", "Koszty suma", "Zysk suma"], stretch_last=False)
        layout.addWidget(self.common_table)
        self.chart_top = QChartView()
        self.chart_top.setRenderHint(QPainter.RenderHint.Antialiasing)

        self.chart_bottom = QChartView()
        self.chart_bottom.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout.addWidget(self.chart_top)
        layout.addWidget(self.chart_bottom)
        for chart_view in (self.chart_top, self.chart_bottom):
            chart_view.setMinimumHeight(320)
        self.firm_list.setMinimumHeight(110)
        self.firm_list.setMaximumHeight(160)

        self.company_label = QLabel("<b>Każda firma osobno</b>")
        layout.addWidget(self.company_label)
        self.company_table = self._make_table(["Firma", "Dział", "Fv suma", "Koszty suma", "Zysk suma"], stretch_last=False)
        layout.addWidget(self.company_table)

        self.import_label = QLabel("<b>Księgowość — szczegóły</b>")
        layout.addWidget(self.import_label)
        self.import_table = self._make_profit_table("ksiegowosc")
        layout.addWidget(self.import_table)
        self.import_label_kadry = QLabel("<b>Kadry — szczegóły</b>")
        layout.addWidget(self.import_label_kadry)
        self.import_table_kadry = self._make_profit_table("kadry")
        layout.addWidget(self.import_table_kadry)

        self.summary_table = QTableWidget(0, 6)
        self.summary_table.setHorizontalHeaderLabels(
            ["Klient", "Księgowa", "Miesiąc", "Typ", "Czas pracy", "Cena za usługę"]
        )
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.summary_table)

        self.report_status = QLabel("Wybierz firmy i zakres, aby wygenerować raport.")
        self.report_status.setStyleSheet(
            "QLabel { background: #f4f6f8; border: 1px solid #d8dde3; "
            "border-radius: 6px; padding: 8px; color: #263238; }"
        )
        layout.addWidget(self.report_status)

        self.report_table = QTableWidget(0, len(REPORT_HEADERS))
        self.report_table.setHorizontalHeaderLabels(REPORT_HEADERS)
        self.report_table.setAlternatingRowColors(True)
        self.report_table.setSortingEnabled(True)
        self.report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.report_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.report_table)

        self.daily_table = self._make_table(DAILY_HEADERS)
        self.daily_table.horizontalHeaderItem(5).setToolTip(
            'Średnia z zapisanych niezerowych miesięcy firmy, do miesiąca rozliczeniowego wpisu włącznie. '
            'Brak niezerowych miesięcy oznaczono kreską. Nie jest to dzienna liczba obsłużonych dokumentów.')
        self.daily_table.horizontalHeaderItem(6).setToolTip('Suma UoP, UZ i UoD w miesiącu rozliczeniowym wpisu.')
        layout.addWidget(self.daily_table)
        self._update_mode_ui(self.mode_combo.currentText())
        for table in (self.summary_table, self.report_table, self.import_table, self.import_table_kadry,
                      self.common_table, self.company_table):
            table.setMinimumHeight(80)

    @staticmethod
    def _make_table(headers, *, stretch_last=True):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(stretch_last)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    @staticmethod
    def _aggregate_profit(rows):
        """Group by stable company identity; names are presentation only."""
        agg = {}
        for item in rows:
            key = (item.get('firma_id', item['klient']), item["klient"], item["miesiac"], item["sekcja"])
            cur = agg.setdefault(key, [0.0, 0.0])
            cur[0] += float(item["cena"] or 0)
            cur[1] += float(item["koszt"] or 0)
        return agg

    @staticmethod
    def _make_profit_table(sekcja):
        headers = PROFIT_HEADERS[sekcja]
        table = ItemTable(headers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        return table

    def _update_mode_ui(self, mode):
        profit = mode == PROFIT_MODE
        daily = mode == DAILY_MODE
        summary = False
        self.month_box_container.setVisible(mode in ["Raport miesięczny", PROFIT_MODE])
        self.firm_list.setVisible(not daily)
        self.daily_box_container.setVisible(daily)
        self.daily_table.setVisible(daily)
        self.year_box_container.setVisible(mode == "Raport roczny")
        self.summary_table.setVisible(summary)
        self.import_table.setVisible(profit)
        self.import_label.setVisible(profit)
        self.import_table_kadry.setVisible(profit)
        self.import_label_kadry.setVisible(profit)
        self.common_label.setVisible(profit)
        self.common_table.setVisible(profit)
        self.company_label.setVisible(profit)
        self.company_table.setVisible(profit)
        self.report_table.setVisible(not summary and not profit and not daily)
        self.chart_top.setVisible(not summary and not daily)
        self.chart_bottom.setVisible(not summary and not daily)

    def _generate_report_async(self):
        self._generate_report(background=True)

    def _generate_report(self, background=False):
        mode = self.mode_combo.currentText()
        actor_id = self.pracownik_id
        if mode == DAILY_MODE:
            request = self._daily_request()
            if request is None:
                return
            ids, start, end = request
            work = lambda: build_daily_report(ids, start, end, actor_id)
            render = self._render_daily
        else:
            selected = [self.firm_list.item(i).data(Qt.ItemDataRole.UserRole)
                        for i in range(self.firm_list.count()) if self.firm_list.item(i).isSelected()]
            if not selected:
                QMessageBox.warning(self, "Brak wyboru", "Wybierz co najmniej jedną firmę.")
                return
            if mode == "Raport roczny":
                year = int(self.year_combo.currentText())
                months = [f"{year}-{m:02d}" for m in range(1, 13)]
            else:
                months = get_months_range(self.month_start.currentText(), self.month_end.currentText())
            if mode == PROFIT_MODE:
                from app.reports.import_dao import build_profitability_report
                work = lambda: build_profitability_report(selected, months, actor_id)
                render = self._render_import_report
            else:
                work = lambda: build_report(selected, months, actor_id)
                render = lambda data: self._render_mixed(data, selected, months)
        if background:
            self.report_status.setText("Przygotowywanie raportu…")
            self._report_jobs.submit(work, render, lambda exc: QMessageBox.warning(self, "Raport", str(exc)))
        else:
            render(work())

    def _reportable_employees(self):
        from app.db.dao_conn import get_conn
        from app.security.authorization import can, is_administrator
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, TRIM(COALESCE(imie, '') || ' ' || COALESCE(nazwisko, '')) AS label "
                "FROM pracownicy ORDER BY nazwisko, imie"
            ).fetchall()
        finally:
            conn.close()
        see_all = (not self.pracownik_id or is_administrator(self.pracownik_id)
                   or can(self.pracownik_id, "can_view_finances"))
        return [(r["id"], r["label"] or f"#{r['id']}") for r in rows if see_all or r["id"] == self.pracownik_id]

    def _daily_from_changed(self):
        if self.daily_to.selectedDate() < self.daily_from.selectedDate():
            self.daily_to.setSelectedDate(self.daily_from.selectedDate())

    def _daily_request(self):
        """Dane raportu dziennego dla zaznaczonych pracowników; None, gdy brak wyboru."""
        ids = [self.employee_list.item(i).data(Qt.ItemDataRole.UserRole)
               for i in range(self.employee_list.count()) if self.employee_list.item(i).isSelected()]
        if not ids:
            QMessageBox.warning(self, "Brak wyboru", "Wybierz co najmniej jednego pracownika.")
            return None
        first = self.daily_from.selectedDate().toPyDate()
        if self.chk_whole_month.isChecked():
            start, end = daily_range(first, True)
        else:
            last = max(first, self.daily_to.selectedDate().toPyDate())
            start, end = first.isoformat(), last.isoformat()
        return ids, start, end

    def _daily_rows(self):
        request = self._daily_request()
        return build_daily_report(*request, self.pracownik_id) if request is not None else None

    def _render_daily(self, rows):
        display = daily_display_rows(rows)
        self.daily_table.setRowCount(len(display))
        for r, values in enumerate(display):
            for c, value in enumerate(values):
                self.daily_table.setItem(r, c, QTableWidgetItem(value))
        self.daily_table.setMinimumHeight(min(600, 40 + 26 * len(display)))
        self.report_status.setText("Brak zarejestrowanego czasu pracy w wybranym zakresie." if not rows
                                   else f"Dni: {len({r['data'] for r in rows})}, pozycji: {len(rows)}.")

    def _profit_rows(self, selected, months):
        from app.reports.import_dao import build_profitability_report

        return build_profitability_report(selected, months, self.pracownik_id)

    @staticmethod
    def _profit_values(item, raw=False):
        if raw:
            return [item["klient"], item["miesiac"], item["pracownik"], item["ilosc"],
                    format_hours(item["sek_glowny"]), item["cena_glowna"],
                    format_hours(item["sek_dodatkowy"]), item["cena_dodatkowa"], item["koszt"], item["zysk"]]
        return [item["klient"], item["miesiac"], item["pracownik"], item["ilosc"],
                format_hours(item["sek_glowny"]), f"{item['cena_glowna']:.2f} zł",
                format_hours(item["sek_dodatkowy"]), f"{item['cena_dodatkowa']:.2f} zł",
                f"{item['koszt']:.2f} zł", f"{item['zysk']:.2f} zł"]

    def _render_import_report(self, rows):
        rows = sorted(rows, key=lambda r: (r['zysk'], r['klient'].casefold(), r['miesiac'], r['pracownik']))
        tables = {"ksiegowosc": self.import_table, "kadry": self.import_table_kadry}
        totals = {}
        for sekcja, table in tables.items():
            records = []
            sek = cena = koszt = 0.0
            for item in rows:
                if item['sekcja'] != sekcja:
                    continue
                values = tuple(str(v) for v in self._profit_values(item))
                roles = {col: {Qt.ItemDataRole.TextAlignmentRole: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter} for col in range(3, len(values))}
                if item['zysk'] < 0:
                    roles[9][Qt.ItemDataRole.ForegroundRole] = QColor('#c62828')
                company_key = item.get('firma_id') if item.get('firma_id') is not None else ('name', item['klient'])
                employee_key = item.get('pracownik_id') if item.get('pracownik_id') is not None else ('name', item['pracownik'])
                key = (company_key, item['miesiac'], sekcja, employee_key)
                records.append(TableRecord(key, values, roles))
                sek += item['sekundy']
                cena += item['cena']
                koszt += item['koszt']
            totals[sekcja] = (sek, cena, koszt)
            table.set_records(records)
            table.resizeColumnsToContents()
            fit_table_height(table)
        if not rows:
            self.common_table.setRowCount(0)
            self.company_table.setRowCount(0)
            self.chart_top.setChart(QChart())
            self.chart_bottom.setChart(QChart())
            self.report_status.setText("Brak zaimportowanych danych dla wybranych firm i zakresu.")
            return
        self._fill_money_table(self.common_table, [
            [SEKCJA_LABELS[s], totals[s][1], totals[s][2]] for s in ("ksiegowosc", "kadry")
        ])
        per_company = {}
        for (company_id, klient, _m, sekcja), (cena, koszt) in self._aggregate_profit(rows).items():
            cur = per_company.setdefault((company_id, klient, sekcja), [0.0, 0.0])
            cur[0] += cena
            cur[1] += koszt
        self._fill_money_table(self.company_table, [
            [klient, SEKCJA_LABELS[sekcja], v[0], v[1]]
            for (_company_id, klient, sekcja), v in per_company.items()
        ])
        months = sorted({item["miesiac"] for item in rows})
        self.chart_top.setChart(self._profit_chart("Kadry — zarobek vs koszt i czas", rows, "kadry", months))
        self.chart_bottom.setChart(
            self._profit_chart("Księgowość — zarobek vs koszt i czas", rows, "ksiegowosc", months))
        self.report_status.setText(" | ".join(
            f"{SEKCJA_LABELS[s]}: czas {format_hours(t[0])}, cena {t[1]:.2f} zł, "
            f"koszt {t[2]:.2f} zł, zysk {t[1] - t[2]:.2f} zł"
            for s, t in totals.items()
        ))

    @staticmethod
    def _fill_money_table(table, rows):
        """Wiersze: [etykiety..., Fv, koszt]; kolumna zysku jest liczona."""
        table.setRowCount(0)
        for values in sorted(rows, key=lambda r: (round(r[-2] - r[-1], 2), tuple(str(v).casefold() for v in r[:-2]))):
            *labels, cena, koszt = values
            zysk = cena - koszt
            row = table.rowCount()
            table.insertRow(row)
            for col, text in enumerate([*labels, f"{cena:.2f} zł", f"{koszt:.2f} zł", f"{zysk:.2f} zł"]):
                cell = QTableWidgetItem(str(text))
                if col >= len(labels):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col == len(labels) + 2 and zysk < 0:
                    cell.setForeground(QColor("#c62828"))
                table.setItem(row, col, cell)
        table.resizeColumnsToContents()
        fit_table_height(table)

    def _profit_chart(self, title, rows, sekcja, months):
        """Słupki obok siebie: zarobek (zielony) i koszt (czerwony); linia: czas pracy w godzinach."""
        from PyQt6.QtCharts import QScatterSeries
        import math

        income = {m: 0.0 for m in months}
        cost = {m: 0.0 for m in months}
        seconds = {m: 0.0 for m in months}
        for item in rows:
            if item["sekcja"] != sekcja:
                continue
            income[item["miesiac"]] += float(item["cena"] or 0)
            cost[item["miesiac"]] += float(item["koszt"] or 0)
            seconds[item["miesiac"]] += float(item["sekundy"] or 0)

        chart = QChart()
        chart.setTitle(title)
        earned = QBarSet("Faktura")
        earned.setColor(QColor("#2e9b4f"))
        spent = QBarSet("Koszt")
        spent.setColor(QColor("#d93025"))
        profit_set = QBarSet("Zysk")
        profit_set.setColor(QColor("#1a73e8"))
        for m in months:
            earned.append(income[m])
            spent.append(cost[m])
            profit_set.append(income[m] - cost[m])
        bars = QBarSeries()
        bars.append(earned)
        bars.append(spent)
        bars.append(profit_set)
        bars.setBarWidth(0.9)
        chart.addSeries(bars)

        axis_x = QBarCategoryAxis()
        axis_x.append(months)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        bars.attachAxis(axis_x)

        profits = [income[m] - cost[m] for m in months]
        axis_y = QValueAxis()
        axis_y.setTitleText("Zł")
        axis_y.setLabelFormat("%.0f")
        axis_y.setMin(min(0.0, math.floor(min(profits, default=0.0) * 1.15)))
        axis_y.setMax(max(1.0, math.ceil(max([*income.values(), *cost.values()], default=0.0) * 1.3)))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        bars.attachAxis(axis_y)

        # linia zera, żeby wartości ujemne wyraźnie wychodziły poniżej niej
        zero = QLineSeries()
        zero.setColor(QColor("#444444"))
        zero.append(-0.5, 0)
        zero.append(len(months) - 0.5, 0)
        chart.addSeries(zero)
        zero.attachAxis(axis_x)
        zero.attachAxis(axis_y)
        for marker in chart.legend().markers(zero):
            marker.setVisible(False)

        line = QLineSeries()
        line.setName("Czas (godziny)")
        pen = line.pen()
        pen.setWidth(2)
        pen.setColor(QColor("#f5a623"))
        line.setPen(pen)
        for idx, m in enumerate(months):
            line.append(idx, seconds[m] / 3600)
        chart.addSeries(line)
        axis_h = QValueAxis()
        axis_h.setTitleText("Czas (godziny)")
        axis_h.setMin(0)
        axis_h.setMax(max(1.0, max(seconds.values(), default=0.0) / 3600 * 1.15))
        chart.addAxis(axis_h, Qt.AlignmentFlag.AlignRight)
        line.attachAxis(axis_x)
        line.attachAxis(axis_h)

        # dodane po linii, więc etykiety kwot są nad nią; pozycje słupków: -0.3, 0, +0.3
        span = axis_y.max() - axis_y.min()
        for idx, m in enumerate(months):
            for offset, value in zip((-0.3, 0.0, 0.3), (income[m], cost[m], income[m] - cost[m])):
                amount = QScatterSeries()
                amount.setMarkerSize(1)
                amount.setColor(QColor(0, 0, 0, 0))
                amount.setBorderColor(QColor(0, 0, 0, 0))
                amount.append(idx + offset, value if value >= 0 else value - span * 0.07)
                amount.setPointLabelsVisible(True)
                amount.setPointLabelsFormat(f"{value:g}")
                amount.setPointLabelsColor(QColor("#000000"))
                amount.setPointLabelsClipping(False)
                f = amount.pointLabelsFont()
                f.setPointSize(10)
                f.setBold(True)
                amount.setPointLabelsFont(f)
                chart.addSeries(amount)
                amount.attachAxis(axis_x)
                amount.attachAxis(axis_y)
                for marker in chart.legend().markers(amount):
                    marker.setVisible(False)

        # etykiety czasu w osobnym rzędzie u góry
        top_y = axis_y.max() * 0.97
        for idx, m in enumerate(months):
            label = QScatterSeries()
            label.setMarkerSize(1)
            label.setColor(QColor(0, 0, 0, 0))
            label.setBorderColor(QColor(0, 0, 0, 0))
            label.append(idx, top_y)
            label.setPointLabelsVisible(True)
            label.setPointLabelsFormat("Czas " + format_hours(seconds[m]))
            label.setPointLabelsColor(QColor("#b36b00"))
            label.setPointLabelsClipping(False)
            chart.addSeries(label)
            label.attachAxis(axis_x)
            label.attachAxis(axis_y)
            for marker in chart.legend().markers(label):
                marker.setVisible(False)
        return chart

    def _import_work(self):
        self._run_import("czas pracy")

    def _import_invoices(self):
        self._run_import("faktury")

    def _run_import(self, kind):
        from PyQt6.QtWidgets import QFileDialog, QInputDialog
        from app.reports import import_dao

        if self._import_busy:
            self.report_status.setText("Import jest już w toku…")
            return
        if not self._can_import():
            QMessageBox.warning(self, "Import", "Brak uprawnień do importu danych.")
            return
        # Modal dialogs dispatch events. Reserve the workflow before opening
        # one so a nested invocation cannot supersede or overlap a write job.
        self._import_busy = True
        submitted = False
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, f"Wybierz plik Excel ({kind})", "", "Pliki Excel (*.xlsx)"
            )
            if not path:
                return
            if kind == "faktury":
                options = [self.month_start.itemText(i) for i in range(self.month_start.count())]
                month, ok = QInputDialog.getItem(self, "Miesiąc faktur", "Miesiąc dla faktur bez miesiąca w numerze:", options, 0, False)
                if not ok:
                    return
                overwrite = self.chk_overwrite.isChecked()
                work = lambda: import_dao.import_invoices_file(path, month, overwrite)
            else:
                work = lambda: import_dao.import_work_file(path)
            self._import_jobs.submit(work, self._import_finished, self._import_failed)
            submitted = True
        except Exception as exc:
            self._import_failed(exc)
        finally:
            if not submitted:
                self._import_busy = False

    def _import_failed(self, exc):
        try:
            QMessageBox.critical(self, "Import", f"Nie udało się zaimportować pliku:\n{exc}")
        finally:
            self._import_busy = False

    def _import_finished(self, result):
        try:
            QMessageBox.information(
                self, "Import",
                f"Wczytano wierszy (od 2026): {result['wiersze']}\n"
                f"Dodano: {result['dodane']}\n"
                f"Pominięto (już zaimportowane): {result['pominiete']}\n"
                f"Bez dopasowanej firmy: {result['bez_firmy']}"
                + (f"\nZaktualizowano cenniki kadr (UoP/UZ/UoD) w firmach: {result['ceny_kadry']}"
                   if result.get("ceny_kadry") else "")
                + (f"\nUzupełniono ilość dokumentów w widoku miesięcznym: {result['ilosc_dok']}"
                   if result.get("ilosc_dok") else ""),
            )
        finally:
            self._import_busy = False

    def _delete_imported(self):
        from PyQt6.QtWidgets import QInputDialog
        from app.reports import import_dao

        if self._import_busy:
            self.report_status.setText("Poczekaj na zakończenie importu przed usunięciem danych.")
            return
        if not self._can_import():
            QMessageBox.warning(self, "Usuwanie", "Brak uprawnień do usuwania danych.")
            return
        labels = {"Czas pracy": "czas", "Faktury": "faktury", "Czas pracy i faktury": "oba"}
        choice, ok = QInputDialog.getItem(
            self, "Usuń zaimportowane dane", "Co usunąć?", list(labels), 0, False
        )
        if not ok:
            return
        months = get_months_range(self.month_start.currentText(), self.month_end.currentText())
        selected = [
            self.firm_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.firm_list.count())
            if self.firm_list.item(i).isSelected()
        ]
        scope = f"{len(selected)} zaznaczonych firm" if selected else "WSZYSTKICH firm (łącznie z niedopasowanymi)"
        if QMessageBox.question(
            self, "Potwierdź usunięcie",
            f"Usunąć „{choice}” z zakresu {months[0]} – {months[-1]} dla {scope}?\n"
            "Tej operacji nie można cofnąć.",
        ) != QMessageBox.StandardButton.Yes:
            return
        # Modal dialogs process events; an import may have started while the
        # deletion choices or confirmation were open.
        if self._import_busy:
            self.report_status.setText("Poczekaj na zakończenie importu przed usunięciem danych.")
            return
        try:
            deleted = import_dao.delete_imported(labels[choice], months, selected or None)
        except Exception as exc:
            logging.getLogger(__name__).exception("Usuwanie zaimportowanych danych nie powiodło się")
            QMessageBox.critical(self, "Usuwanie", f"Nie udało się usunąć danych:\n{exc}")
            return
        QMessageBox.information(self, "Usuwanie", f"Usunięto wierszy: {deleted}")

    def _can_import(self):
        from app.security.authorization import can, is_administrator

        return is_administrator(self.pracownik_id) or can(self.pracownik_id, "can_view_finances")

    def _edit_rates(self):
        from app.reports import import_dao

        if not self._can_import():
            QMessageBox.warning(self, "Stawki", "Brak uprawnień do edycji stawek.")
            return
        employees = import_dao.list_employee_rates()
        dlg = QDialog(self)
        dlg.setWindowTitle("Stawki godzinowe pracowników (koszt pracy, zł/h)")
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(employees), 2)
        table.setHorizontalHeaderLabels(["Pracownik", "Stawka zł/h (uśredniona)"])
        table.horizontalHeader().setStretchLastSection(True)
        for i, emp in enumerate(employees):
            name = QTableWidgetItem(emp["nazwa"])
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name.setData(Qt.ItemDataRole.UserRole, emp["id"])
            table.setItem(i, 0, name)
            table.setItem(i, 1, QTableWidgetItem("" if emp["stawka"] is None else f"{emp['stawka']:.2f}"))
        lay.addWidget(table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        if not dlg.exec():
            return
        rates = {}
        for i in range(table.rowCount()):
            raw = (table.item(i, 1).text() or "").strip().replace(",", ".")
            try:
                value = float(raw) if raw else None
            except ValueError:
                QMessageBox.warning(self, "Stawki", f"Niepoprawna stawka: {raw}")
                return
            rates[table.item(i, 0).data(Qt.ItemDataRole.UserRole)] = value
        import_dao.set_employee_rates(rates)

    def _render_mixed(self, data, selected, months):
        selected = [fid for fid in selected if fid in data]
        self._render_report_table(data, selected, months)
        self.chart_top.setChart(self._count_time_chart(
            "Kadry — ilość umów vs czas", "Ilość umów", data, selected, months, "umowy", "godziny_kadry"))
        self.chart_bottom.setChart(self._count_time_chart(
            "Księgowość — ilość dokumentów vs czas", "Ilość dokumentów", data, selected, months,
            "dokumenty", "godziny_ksiegowosc"))

    def _count_time_chart(self, title, count_title, data, selected, months, count_key, seconds_key):
        """Słupki = liczba (dokumentów/umów), linia = czas pracy; etykiety czasu w formacie H:MM."""
        from PyQt6.QtCharts import QScatterSeries

        chart = QChart()
        chart.setTitle(title)

        bars = QBarSeries()
        for fid in selected:
            bar = QBarSet(self.company_names.get(fid, f"Firma {fid}"))
            for m in months:
                bar.append(float(data[fid][m].get(count_key, 0) or 0))
            bars.append(bar)
        chart.addSeries(bars)

        axis_x = QBarCategoryAxis()
        axis_x.append(months)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        bars.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setTitleText(count_title)
        axis_y.setLabelFormat("%d")
        axis_y.setMin(0)
        # Zakres musi obejmować liczbę dokumentów, także gdy firma nie ma
        # limitu dokumentów lub dokumentów jest więcej niż 10.
        import math
        max_count = max(
            (float(data[fid][m].get(count_key, 0) or 0)
             for fid in selected for m in months),
            default=0.0,
        )
        axis_y.setMax(max(1, math.ceil(max_count * 1.15)))
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        bars.attachAxis(axis_y)

        axis_y2 = QValueAxis()
        axis_y2.setTitleText("Czas (godziny)")
        axis_y2.setMin(0)
        chart.addAxis(axis_y2, Qt.AlignmentFlag.AlignRight)

        max_hours = 0.0
        for fid in selected:
            line = QLineSeries()
            line.setName(f"Czas — {self.company_names.get(fid, fid)}" if len(selected) > 1 else "Czas (godziny)")
            seconds_by_month = []
            for idx, m in enumerate(months):
                seconds = float(data[fid][m].get(seconds_key, 0) or 0)
                seconds_by_month.append(seconds)
                line.append(idx, seconds / 3600)
            chart.addSeries(line)
            line.attachAxis(axis_x)
            line.attachAxis(axis_y2)
            max_hours = max(max_hours, max(seconds_by_month, default=0) / 3600)
            # jedna seria na punkt, bo format etykiety (H:MM) jest stały dla całej serii
            for idx, seconds in enumerate(seconds_by_month):
                point = QScatterSeries()
                point.setMarkerSize(7)
                point.append(idx, seconds / 3600)
                point.setPointLabelsVisible(True)
                point.setPointLabelsFormat(format_hours(seconds))
                point.setPointLabelsClipping(False)
                chart.addSeries(point)
                point.attachAxis(axis_x)
                point.attachAxis(axis_y2)
                for marker in chart.legend().markers(point):
                    marker.setVisible(False)
        axis_y2.setMax(max(max_hours * 1.15, 1.0))
        return chart

    def _render_report_table(self, data, selected, months):
        self.report_table.setSortingEnabled(False)
        self.report_table.setRowCount(0)
        total_docs = total_contracts = 0
        total_ks_time = total_kadry_time = 0.0
        for fid in selected:
            for month in months:
                row_data = data[fid][month]
                row = self.report_table.rowCount()
                self.report_table.insertRow(row)
                values = [
                    self.company_names.get(fid, f"Firma {fid}"),
                    month,
                    int(row_data.get("dokumenty", 0) or 0),
                    format_hours(row_data.get("godziny_ksiegowosc", 0)),
                    int(row_data.get("umowy", 0) or 0),
                    format_hours(row_data.get("godziny_kadry", 0)),
                ]
                for column, value in enumerate(values):
                    self.report_table.setItem(row, column, QTableWidgetItem(str(value)))
                total_docs += int(row_data.get("dokumenty", 0) or 0)
                total_contracts += int(row_data.get("umowy", 0) or 0)
                total_ks_time += float(row_data.get("godziny_ksiegowosc", 0) or 0)
                total_kadry_time += float(row_data.get("godziny_kadry", 0) or 0)
        self.report_table.setSortingEnabled(True)
        fit_table_height(self.report_table)
        self.report_status.setText(
            f"Podsumowanie: księgowość {total_docs} dokumentów / {format_hours(total_ks_time)}, "
            f"kadry {total_contracts} umów (suma miesięcy) / {format_hours(total_kadry_time)}"
        )

    # ============================================================
    # ZAROBEK NA GODZINĘ — SŁUPKI
    # ============================================================

    def _export_to_excel(self, background=False):
        """Capture controls on the GUI thread; query and write in the export queue."""
        if getattr(self, "_export_busy", False):
            return
        self._export_busy = True
        submitted = False
        try:
            mode = self.mode_combo.currentText()
            actor = self.pracownik_id
            if mode == DAILY_MODE:
                request = self._daily_request()
                if request is None:
                    return
                def rows():
                    return iter_daily_display_rows(build_daily_report(*request, actor))
                title, headers = "Raport dzienny", DAILY_HEADERS
            else:
                selected = [self.firm_list.item(i).data(Qt.ItemDataRole.UserRole)
                            for i in range(self.firm_list.count()) if self.firm_list.item(i).isSelected()]
                if not selected:
                    QMessageBox.warning(self, "Błąd", "Wybierz firmę przed eksportem.")
                    return
                if mode == "Raport roczny":
                    year = int(self.year_combo.currentText())
                    months = [f"{year}-{m:02d}" for m in range(1, 13)]
                else:
                    months = get_months_range(self.month_start.currentText(), self.month_end.currentText())
                if mode == PROFIT_MODE:
                    title, headers = "Rentowność", ["Firma", "Miesiąc", "Dział", "Sprzedaż", "Koszt", "Zysk"]
                    def rows():
                        from app.reports.import_dao import build_profitability_report
                        agg = ReportsPage._aggregate_profit(build_profitability_report(selected, months, actor))
                        for (_company_id, company, month, section), (price, cost) in sorted(
                                agg.items(), key=lambda kv: (round(kv[1][0] - kv[1][1], 2), kv[0])):
                            yield [company, month, SEKCJA_LABELS[section], round(price, 2), round(cost, 2), round(price-cost, 2)]
                else:
                    title = "Raport"
                    headers = ["Firma ID", "Miesiąc", "Ilość dokumentów", "Ilość umów", "Kwota Kadry", "Kwota Księgowość",
                               "Czas Kadry (H:MM)", "Czas Księgowość (H:MM)", "Zarobek/h Kadry", "Zarobek/h Księgowość"]
                    def rows():
                        data = build_report(selected, months, actor)
                        for fid in selected:
                            for month in months:
                                row = data[fid][month]
                                yield [fid, month, row.get("dokumenty", ""), row.get("umowy", ""),
                                       row.get("koszt_kadry", row.get("profit_kadry", 0)),
                                       row.get("koszt_ksiegowosc", row.get("profit_ksiegowosc", 0)),
                                       format_hours(row.get("godziny_kadry", 0)), format_hours(row.get("godziny_ksiegowosc", 0)),
                                       round(row.get("profit_kadry", row.get("koszt_na_godzine_kadry", 0)), 2),
                                       round(row.get("profit_ksiegowosc", row.get("koszt_na_godzine_ksiegowosc", 0)), 2)]
            filepath, _ = QFileDialog.getSaveFileName(self, "Zapisz raport Excel", "", "Pliki Excel (*.xlsx)")
            if not filepath:
                return
            def work():
                from app.reports.excel_export import write_report
                write_report(filepath, title, headers, rows())
                return filepath
            if background:
                self._export_jobs.submit(work, self._export_finished, self._export_failed)
                submitted = True
            else:
                self._export_finished(work())
        except Exception as exc:
            self._export_failed(exc)
        finally:
            if not submitted:
                self._export_busy = False

    def _export_finished(self, filepath):
        try:
            QMessageBox.information(self, "Sukces", f"Raport zapisany:\n{filepath}")
        finally:
            self._export_busy = False

    def _export_failed(self, exc):
        try:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać pliku:\n{exc}")
        finally:
            self._export_busy = False
