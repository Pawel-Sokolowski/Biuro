# app/faktury/faktury_logic.py
import sqlite3
from typing import List, Dict, Any, Optional
import json
import datetime

from app.db.dao_conn import get_conn
from app.db.dao_faktury import create_or_update_invoice


class FakturyLogic:

    # ============================================================
    # Pobranie danych miesięcznych firmy
    # ============================================================
    def _get_month_status(self, firma_id: int, miesiac: str) -> Optional[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM statusy_miesieczne
            WHERE firma_id = ? AND miesiac = ?
        """, (firma_id, miesiac))
        row = cur.fetchone()
        conn.close()
        return row

    # ============================================================
    # Pobranie usług dodatkowych
    # ============================================================
    def _get_extra_services(self, firma_id: int, miesiac: str) -> List[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.nazwa, u.kategoria, u.stawka_vat, uf.ilosc, uf.cena_netto
            FROM uslugi_dodatkowe_firma uf
            JOIN uslugi_dodatkowe u ON uf.usluga_id = u.id
            WHERE uf.firma_id = ? AND uf.miesiac = ?
            ORDER BY u.nazwa
        """, (firma_id, miesiac))
        rows = cur.fetchall()
        conn.close()
        return rows

    # ============================================================
    # Pobranie danych firmy
    # ============================================================
    def _get_company(self, firma_id: int) -> Optional[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nazwa, nip, miasto, glowny_cennik, limit_dokumentow,
                   typ_rozliczenia, vat_czynny
            FROM firmy
            WHERE id = ?
        """, (firma_id,))
        row = cur.fetchone()
        conn.close()
        return row

    # ============================================================
    # Pobranie cennika głównego
    # ============================================================
    def _get_main_pricing(self, klucz: str) -> Optional[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM cenniki
            WHERE klucz = ?
        """, (klucz,))
        row = cur.fetchone()
        conn.close()
        return row

    # ============================================================
    # Generowanie pozycji faktury
    # ============================================================
    def generate_invoice_positions(self, firma_id: int, miesiac: str) -> List[Dict[str, Any]]:
        company = self._get_company(firma_id)
        if not company:
            return []

        status = self._get_month_status(firma_id, miesiac)
        extra = self._get_extra_services(firma_id, miesiac)

        pozycje: List[Dict[str, Any]] = []

        # ------------------------------------------------------------
        # 1. Usługa księgowa (ryczałt)
        # ------------------------------------------------------------
        if company["glowny_cennik"]:
            pricing = self._get_main_pricing(company["glowny_cennik"])
            if pricing:
                netto = float(pricing["cena_netto_ksiegowosc"])
                vat = 23
                brutto = netto * (1 + vat / 100)

                pozycje.append({
                    "nazwa": "Usługa księgowa",
                    "ilosc": 1,
                    "jednostka": "mc",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "księgowość"
                })

        # ------------------------------------------------------------
        # 2. Dokumenty ponad limit
        # ------------------------------------------------------------
        if status:
            ilosc_dok = status["ilosc_dok"]
            limit = company["limit_dokumentow"] or 0

            if ilosc_dok > limit:
                ponad = ilosc_dok - limit
                pricing = self._get_main_pricing(company["glowny_cennik"])
                if pricing:
                    cena = float(pricing["cena_za_dokument_ponad_limit"])
                    netto = ponad * cena
                    vat = 23
                    brutto = netto * (1 + vat / 100)

                    pozycje.append({
                        "nazwa": "Dokumenty ponad limit",
                        "ilosc": ponad,
                        "jednostka": "szt.",
                        "netto": netto,
                        "vat": vat,
                        "brutto": brutto,
                        "kategoria": "księgowość"
                    })

        # ------------------------------------------------------------
        # 3. Zdrowotna
        # ------------------------------------------------------------
        if company["glowny_cennik"]:
            pricing = self._get_main_pricing(company["glowny_cennik"])
            if pricing and pricing["zdrowotna"] > 0:
                netto = float(pricing["zdrowotna"])
                vat = 0
                brutto = netto

                pozycje.append({
                    "nazwa": "Zdrowotna",
                    "ilosc": 1,
                    "jednostka": "mc",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "księgowość"
                })

        # ------------------------------------------------------------
        # 4. Usługi dodatkowe
        # ------------------------------------------------------------
        for u in extra:
            if u["ilosc"] == 0:
                continue

            netto = float(u["cena_netto"]) * u["ilosc"]
            vat = int(u["stawka_vat"].replace("%", ""))
            brutto = netto * (1 + vat / 100)

            pozycje.append({
                "nazwa": u["nazwa"],
                "ilosc": u["ilosc"],
                "jednostka": "szt.",
                "netto": netto,
                "vat": vat,
                "brutto": brutto,
                "kategoria": u["kategoria"]
            })

        # ------------------------------------------------------------
        # 5. Stoper księgowość / kadry
        # ------------------------------------------------------------
        if status:
            # księgowość
            if status["sekundy_ksiegowosc"] > 0:
                godz = status["sekundy_ksiegowosc"] / 3600
                netto = round(godz * 150, 2)
                vat = 23
                brutto = netto * (1 + vat / 100)

                pozycje.append({
                    "nazwa": "Praca księgowa",
                    "ilosc": round(godz, 2),
                    "jednostka": "godz.",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "księgowość"
                })

            # kadry
            if status["sekundy_kadry"] > 0:
                godz = status["sekundy_kadry"] / 3600
                netto = round(godz * 120, 2)
                vat = 23
                brutto = netto * (1 + vat / 100)

                pozycje.append({
                    "nazwa": "Praca kadrowa",
                    "ilosc": round(godz, 2),
                    "jednostka": "godz.",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "kadry"
                })

        return pozycje

    # ============================================================
    # Generowanie faktury i zapis do DB
    # ============================================================
    def generate_and_save_invoice(self, firma_id: int, miesiac: str) -> Dict[str, Any]:
        pozycje = self.generate_invoice_positions(firma_id, miesiac)

        kwota_netto = sum(p["netto"] for p in pozycje)
        kwota_brutto = sum(p["brutto"] for p in pozycje)

        invoice_id = create_or_update_invoice(
            firma_id=firma_id,
            miesiac=miesiac,
            pozycje=pozycje,
            kwota_netto=kwota_netto,
            kwota_brutto=kwota_brutto
        )

        return {
            "id": invoice_id,
            "firma_id": firma_id,
            "miesiac": miesiac,
            "pozycje": pozycje,
            "kwota_netto": kwota_netto,
            "kwota_brutto": kwota_brutto
        }
