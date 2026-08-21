from app.companies.constants import COMPANY_STAGE_OPTIONS
from app.db.dao_companies import get_conn
from app.utils.crypto import is_admin
from app.utils.saldeo_exporter import apply_default_main_pricing, wybierz_cennik, pobierz_cene_za_dokument
from app.utils.ui_imports import *
from app.config import DB_NAME


def refresh_monthly_filters(self):
    """Populate employee filter combobox with active employees (Wszyscy + list)."""
    self.month_employee_filter.blockSignals(True)
    self.month_employee_filter.clear()
    self.month_employee_filter.addItem("Wszyscy", None)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, imie, nazwisko
            FROM pracownicy
            WHERE aktywny = 1
            ORDER BY nazwisko, imie
        """)
        for _id, imie, nazwisko in cur.fetchall():
            label = f"{imie} {nazwisko}"
            self.month_employee_filter.addItem(label, _id)
    finally:
        conn.close()

    self.month_employee_filter.blockSignals(False)


def _change_month(self, new_month: str):
    self.aktualny_miesiac = new_month
    self.refresh_monthly_table()


def refresh_monthly_table(self):
    selected_month = self.month_selector.currentText() if getattr(self, "month_selector",
                                                                  None) else self.aktualny_miesiac
    selected_emp_id = self.month_employee_filter.currentData() if getattr(self, "month_employee_filter",
                                                                          None) else None

    conn = get_conn()
    cur = conn.cursor()

    if is_admin(self.pracownik_id):
        if selected_emp_id:
            cur.execute("""
                SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                       COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                FROM firmy f
                JOIN pracownik_firma pf ON pf.firma_id = f.id
                LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                WHERE pf.pracownik_id = ?
                GROUP BY f.id
                ORDER BY f.nazwa
            """, (selected_month, selected_emp_id))
        else:
            cur.execute("""
                SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                       COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
                FROM firmy f
                LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
                ORDER BY f.nazwa
            """, (selected_month,))
    else:
        cur.execute("""
            SELECT f.id, f.nazwa, COALESCE(sm.etap, 'Brak'),
                   COALESCE(sm.ilosc_dok, 0), COALESCE(f.sekundy_pracy, 0)
            FROM firmy f
            JOIN pracownik_firma pf ON pf.firma_id = f.id
            LEFT JOIN statusy_miesieczne sm ON sm.firma_id = f.id AND sm.miesiac = ?
            WHERE pf.pracownik_id = ?
            GROUP BY f.id
            ORDER BY f.nazwa
        """, (selected_month, self.pracownik_id))

    rows = cur.fetchall()
    conn.close()

    self.table_monthly.setRowCount(0)
    self.status_widgets = {}

    for row_idx, row in enumerate(rows):
        f_id, nazwa, etap, ilosc_dok, sekundy = row
        self.table_monthly.insertRow(row_idx)

        # 0: ID
        self.table_monthly.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))

        # 1: Nazwa
        self.table_monthly.setItem(row_idx, 1, QTableWidgetItem(nazwa or ""))

        # 2: Etap
        cb_etap = QComboBox()
        cb_etap.addItems(COMPANY_STAGE_OPTIONS)
        cb_etap.setCurrentText(etap or "Brak")
        cb_etap.currentTextChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
        self.table_monthly.setCellWidget(row_idx, 2, cb_etap)

        # 3: Ilość dokumentów
        txt_docs = QLineEdit(str(ilosc_dok))
        txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt_docs.setFixedWidth(80)
        txt_docs.textChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
        self.table_monthly.setCellWidget(row_idx, 3, txt_docs)

        # 4: Czas pracy
        czas_format = str(datetime.timedelta(seconds=sekundy or 0))
        lbl_time = QLabel(czas_format)
        lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table_monthly.setCellWidget(row_idx, 4, lbl_time)

        # 5: Stoper
        btn_stoper = QPushButton("▶" if self.aktywna_firma_stoper != f_id else "⏸")
        btn_stoper.setFixedSize(36, 28)
        btn_stoper.setToolTip("Start / Stop")
        if self.aktywna_firma_stoper == f_id:
            btn_stoper.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        else:
            btn_stoper.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
        btn_stoper.clicked.connect(lambda checked, fid=f_id, btn=btn_stoper: self._toggle_stoper_button(fid, btn))
        self.table_monthly.setCellWidget(row_idx, 5, btn_stoper)

        # 6: Notatka (skrót)
        conn2 = get_conn()
        cur2 = conn2.cursor()
        cur2.execute("""
            SELECT tresc, data_utworzenia
            FROM company_notes
            WHERE firma_id = ?
            ORDER BY data_utworzenia DESC LIMIT 1
        """, (f_id,))
        note_row = cur2.fetchone()
        conn2.close()

        if note_row:
            tresc, dt = note_row
            snippet = (tresc[:120] + "…") if len(tresc) > 120 else tresc
            lbl_note = QLabel(snippet)
            lbl_note.setToolTip(str(dt))
        else:
            lbl_note = QLabel("")

        lbl_note.setWordWrap(True)
        lbl_note.mouseDoubleClickEvent = lambda event, fid=f_id: self._open_notes_window(fid)
        self.table_monthly.setCellWidget(row_idx, 6, lbl_note)

        # 7: Usługa dodatkowa
        btn_usluga = QPushButton("Usługa")
        btn_usluga.clicked.connect(lambda checked, fid=f_id: self.dodaj_usluge_okno(fid))
        self.table_monthly.setCellWidget(row_idx, 7, btn_usluga)

        # 8: Edytuj firmę
        btn_actions = QPushButton("Edytuj")
        btn_actions.clicked.connect(lambda checked, fid=f_id: self._on_edit_company_from_table())
        self.table_monthly.setCellWidget(row_idx, 8, btn_actions)

        self.status_widgets[f_id] = (txt_docs, cb_etap)
# -------------------------
# Timer / stoper helpers
# -------------------------
def _toggle_stoper_button(self, firma_id: int, btn: QPushButton):
    if self.aktywna_firma_stoper is None:
        self.obsluga_stopera(firma_id, None)
        btn.setText("⏸")
        btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        return

    if self.aktywna_firma_stoper == firma_id:
        self.obsluga_stopera(firma_id, None)
        btn.setText("▶")
        btn.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
        return

    # switch to another company
    self.obsluga_stopera(firma_id, None)
    self.refresh_monthly_table()


def obsluga_stopera(self, firma_id: int, row_idx: Optional[int]):
    conn = get_conn()
    cur = conn.cursor()

    if self.aktywna_firma_stoper is None:
        self.aktywna_firma_stoper = firma_id
        self.czas_start = datetime.datetime.now()
        cur.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
        res = cur.fetchone()
        nazwa = res[0] if res else str(firma_id)
        self.aktywna_firma_stoper_nazwa = nazwa
        self.lbl_global_timer_status.setText(f"Timer: {nazwa}")
    elif self.aktywna_firma_stoper == firma_id:
        roznica_sekund = int((datetime.datetime.now() - self.czas_start).total_seconds())
        cur.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (roznica_sekund, firma_id))
        conn.commit()
        self.aktywna_firma_stoper = None
        self.aktywna_firma_stoper_nazwa = ""
        self.czas_start = None
        self.lbl_global_timer_status.setText("Timer: brak aktywnej firmy")
    else:
        previous_company_id = self.aktywna_firma_stoper
        previous_delta = int((datetime.datetime.now() - self.czas_start).total_seconds())
        cur.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?",
                    (previous_delta, previous_company_id))
        self.aktywna_firma_stoper = firma_id
        self.czas_start = datetime.datetime.now()
        cur.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
        res = cur.fetchone()
        nazwa = res[0] if res else str(firma_id)
        self.aktywna_firma_stoper_nazwa = nazwa
        self.lbl_global_timer_status.setText(f"Timer: {nazwa}")
        conn.commit()

    conn.close()
    self.refresh_monthly_table()


def aktualizuj_stoper_gui(self):
    if self.aktywna_firma_stoper is not None and self.czas_start is not None:
        roznica = int((datetime.datetime.now() - self.czas_start).total_seconds())
        self.lbl_global_timer_status.setText(
            f"Timer: {self.aktywna_firma_stoper_nazwa or f'firma #{self.aktywna_firma_stoper}'} — {roznica} s"
        )
    else:
        self.lbl_global_timer_status.setText("Timer: brak aktywnej firmy")


def schedule_status_autosave(self, firma_id: int):
    timer = self.status_autosave_timers.get(firma_id)
    if timer is None:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda fid=firma_id: self.zapisz_wiersz_statusu(fid, show_feedback=False))
        self.status_autosave_timers[firma_id] = timer
    timer.start(700)


def zapisz_wiersz_statusu(self, firma_id: int, show_feedback: bool = True):
    txt_widget, cb_widget = self.status_widgets.get(firma_id, (None, None))
    txt_docs = txt_widget.text() if txt_widget is not None else "0"
    cb_etap = cb_widget.currentText() if cb_widget is not None else "Brak"

    try:
        ilosc_dok = int(txt_docs)
    except ValueError:
        if show_feedback:
            QMessageBox.critical(self, "Błąd", "Pole ilość dokumentów musi posiadać wartość liczbową!")
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO statusy_miesieczne (firma_id, miesiac, etap, ilosc_dok)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(firma_id, miesiac) DO UPDATE SET etap = excluded.etap, ilosc_dok = excluded.ilosc_dok
        """, (firma_id, self.aktualny_miesiac, cb_etap, ilosc_dok)
    )
    apply_default_main_pricing(cur, firma_id, ilosc_dok)
    conn.commit()
    conn.close()
    if show_feedback:
        QMessageBox.information(self, "Zapisano", "Stan firmy został zaktualizowany pomyślnie.")
    else:
        try:
            self.statusBar().showMessage("Status firmy zapisano automatycznie.", 1500)
        except Exception:
            pass


def dodaj_usluge_okno(self, firma_id: int):
    conn = get_conn()
    cur = conn.cursor()

    # Pobranie danych firmy
    cur.execute("SELECT typ_rozliczenia, vat_czynny, limit_dokumentow FROM firmy WHERE id = ?", (firma_id,))
    firma = cur.fetchone()
    if not firma:
        QMessageBox.critical(self, "Błąd", "Nie znaleziono firmy.")
        return

    typ = firma["typ_rozliczenia"]
    vat = bool(firma["vat_czynny"])
    limit = firma["limit_dokumentow"]

    # Pobranie cennika głównego
    cennik = wybierz_cennik(conn, typ, vat, limit)
    cena_za_dok = pobierz_cene_za_dokument(conn, typ, vat)

    # Lista usług dodatkowych
    uslugi = [
        ("Usługa księgowa", cennik["cena_netto_2024"]),
        ("Usługa kadrowa", cennik["zdrowotna"]),
        ("Pojedynczy dokument", cena_za_dok),
        ("Doradztwo IT", 250.00),
        ("Obsługa ZUS", 100.00),
        ("Inna usługa", 0.00)
    ]

    # Okno dialogowe
    dialog = QDialog(self)
    dialog.setWindowTitle("Dodaj usługę dodatkową")
    layout = QVBoxLayout(dialog)

    cb_usluga = QComboBox()
    for nazwa, cena in uslugi:
        cb_usluga.addItem(nazwa, cena)
    layout.addWidget(QLabel("Rodzaj usługi:"))
    layout.addWidget(cb_usluga)

    txt_cena = QLineEdit()
    txt_cena.setPlaceholderText("Cena netto")
    layout.addWidget(QLabel("Cena netto:"))
    layout.addWidget(txt_cena)

    def on_usluga_changed():
        cena = cb_usluga.currentData()
        txt_cena.setText(str(cena))

    cb_usluga.currentIndexChanged.connect(on_usluga_changed)
    on_usluga_changed()

    btn_ok = QPushButton("Dodaj")
    btn_ok.setProperty("class", "primary")
    btn_ok.clicked.connect(dialog.accept)
    layout.addWidget(btn_ok)

    if dialog.exec() != QDialog.DialogCode.Accepted:
        conn.close()
        return

    # Pobranie danych z okna
    nazwa_uslugi = cb_usluga.currentText()
    try:
        cena_netto = float(txt_cena.text().replace(",", "."))
    except ValueError:
        QMessageBox.critical(self, "Błąd", "Cena musi być liczbą.")
        conn.close()
        return

    # Numer dokumentu
    nr_faktury = f"USL/{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}/{firma_id}"

    # Zapis do tabeli dokumenty
    cur.execute(
        "INSERT INTO dokumenty (firma_id, numer, typ, cena_netto, data_wyst) VALUES (?, ?, ?, ?, ?)",
        (firma_id, nr_faktury, nazwa_uslugi, cena_netto, datetime.datetime.now().strftime("%Y-%m-%d"))
    )

    # Aktualizacja statusu miesięcznego
    cur.execute("""
        INSERT INTO statusy_miesieczne (firma_id, miesiac, ilosc_dok)
        VALUES (?, ?, 1)
        ON CONFLICT(firma_id, miesiac) DO UPDATE SET ilosc_dok = ilosc_dok + 1
    """, (firma_id, self.aktualny_miesiac))

    cur.execute("SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id = ? AND miesiac = ?",
                (firma_id, self.aktualny_miesiac))
    document_count = cur.fetchone()
    apply_default_main_pricing(cur, firma_id, document_count[0] if document_count else 0)

    conn.commit()
    conn.close()

    self.refresh_monthly_table()
    QMessageBox.information(self, "Dodano usługę", f"Utworzono usługę: {nazwa_uslugi}\nZapisano do raportu Saldeo.")
