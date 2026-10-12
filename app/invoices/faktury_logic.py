# app/invoices/faktury_logic.py
import json
import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    get_invoice_for_company_month,
    create_or_update_invoice
)
from app.db.dao_monthly import count_documents_for_month
from app.utils.months import previous_month


class FakturyLogic:
    _NON_BILLABLE_TIME_LINES = {"praca księgowa", "praca kadrowa"}

    # ---------------- STATUS MIESIĘCZNY ----------------
    def _get_month_status(self, firma_id: int, miesiac: str) -> Optional[dict]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM statusy_miesieczne
            WHERE firma_id = ? AND miesiac = ?
        """, (firma_id, miesiac))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def _get_document_count_for_invoice(self, firma_id: int, miesiac: str, fallback: int) -> int:
        document_month = previous_month(miesiac)
        imported_count = count_documents_for_month(firma_id, document_month)
        if imported_count:
            return imported_count
        previous_status = self._get_month_status(firma_id, document_month)
        if previous_status and previous_status.get("ilosc_dok") is not None:
            return int(previous_status.get("ilosc_dok") or 0)
        return int(fallback or 0)

    # ---------------- USŁUGI DODATKOWE ----------------
    def _get_extra_services(self, firma_id: int, miesiac: str) -> List[dict]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT u.nazwa, u.kategoria, u.stawka_vat, uf.ilosc, uf.cena_netto
            FROM uslugi_dodatkowe_firma uf
            JOIN uslugi_dodatkowe u ON uf.usluga_id = u.id
            WHERE uf.firma_id = ? AND uf.miesiac = ? AND uf.fakturowac = 1
            ORDER BY u.nazwa
        """, (firma_id, miesiac))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows] if rows else []

    # ---------------- FIRMA ----------------
    def _get_company(self, firma_id: int) -> Optional[dict]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM firmy WHERE id = ?", (firma_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None

        company = dict(row)

        # tylko pola istniejące w schema.py
        company.setdefault("id", firma_id)
        company.setdefault("nazwa", company.get("nazwa", "<brak nazwy>"))
        company.setdefault("glowny_cennik", company.get("glowny_cennik"))
        company.setdefault("limit_dokumentow", int(company.get("limit_dokumentow") or 0))
        company.setdefault("zdrowotna", float(company.get("zdrowotna") or 0.0))
        company.setdefault("vat_czynny", int(company.get("vat_czynny") or 0))
        company.setdefault("typ_rozliczenia", company.get("typ_rozliczenia", "Ryczałt"))
        company.setdefault("fakturowac", int(company.get("fakturowac") or 0))

        # pola kadrowe
        company.setdefault("uop_ilosc", int(company.get("uop_ilosc") or 0))
        company.setdefault("uz_ilosc", int(company.get("uz_ilosc") or 0))
        company.setdefault("uod_ilosc", int(company.get("uod_ilosc") or 0))
        company.setdefault("uop_cennik", company.get("uop_cennik"))
        company.setdefault("uz_cennik", company.get("uz_cennik"))
        company.setdefault("uod_cennik", company.get("uod_cennik"))

        return company

    # ---------------- CENNIK ----------------
    def _get_main_pricing(self, klucz: str) -> Optional[dict]:
        if not klucz:
            return None
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT
                klucz,
                cena_netto_ksiegowosc,
                cena_brutto_ksiegowosc,
                cena_za_dokument_ponad_limit,
                zdrowotna,
                netto,
                brutto
            FROM cenniki
            WHERE klucz = ?
        """, (klucz,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    # ---------------- HELPER ----------------
    def _to_float(self, v, default: float = 0.0) -> float:
        try:
            if v is None or v == "":
                return float(default)
            return float(v)
        except Exception:
            return float(default)

    # ---------------- GENEROWANIE POZYCJI ----------------
    def generate_invoice_positions(self, firma_id: int, miesiac: str) -> List[Dict[str, Any]]:
        company = self._get_company(firma_id)
        if not company:
            return []

        status = self._get_month_status(firma_id, miesiac)
        extra = self._get_extra_services(firma_id, miesiac)

        pozycje: List[Dict[str, Any]] = []

        glowny = company.get("glowny_cennik")
        pricing = self._get_main_pricing(glowny) if glowny else None

        # ---------------- Usługa księgowa ----------------
        if pricing and company.get("fakturowac", 0) == 1:
            netto = self._to_float(pricing.get("cena_netto_ksiegowosc", 0.0))
            brutto = self._to_float(pricing.get("cena_brutto_ksiegowosc", round(netto * 1.23, 2)))
            pozycje.append({
                "nazwa": "Usługa księgowa",
                "ilosc": 1,
                "jednostka": "mc",
                "netto": netto,
                "vat": 23,
                "brutto": brutto,
                "kategoria": "księgowość",
                "generated": True
            })

        # ---------------- Dokumenty ponad limit ----------------
        if pricing and company.get("fakturowac", 0) == 1:
            ilosc_dok = self._get_document_count_for_invoice(
                firma_id, miesiac, int((status or {}).get("ilosc_dok") or 0)
            )
            limit = int(company.get("limit_dokumentow") or 0)
            if ilosc_dok > limit:
                ponad = ilosc_dok - limit
                cena = self._to_float(pricing.get("cena_za_dokument_ponad_limit", 0.0))
                netto = round(ponad * cena, 2)
                brutto = round(netto * 1.23, 2)
                pozycje.append({
                    "nazwa": "Dokumenty ponad limit",
                    "ilosc": ponad,
                    "jednostka": "szt.",
                    "netto": netto,
                    "vat": 23,
                    "brutto": brutto,
                    "kategoria": "księgowość",
                    "generated": True
                })

        # ---------------- Zdrowotna ----------------
        if pricing and company.get("fakturowac", 0) == 1:
            zdrowotna_val = self._to_float(pricing.get("zdrowotna", 0.0))
            if zdrowotna_val > 0:
                pozycje.append({
                    "nazwa": "Zdrowotna",
                    "ilosc": 1,
                    "jednostka": "mc",
                    "netto": zdrowotna_val,
                    "vat": 0,
                    "brutto": zdrowotna_val,
                    "kategoria": "księgowość",
                    "generated": True
                })

        # ---------------- Usługi dodatkowe (zawsze) ----------------
        for u in extra:
            ilosc = int(u.get("ilosc") or 0)
            if ilosc == 0:
                continue
            cena_netto = self._to_float(u.get("cena_netto", 0.0))
            netto = round(cena_netto * ilosc, 2)
            stawka_raw = u.get("stawka_vat") or "0"
            if isinstance(stawka_raw, str):
                stawka_raw = stawka_raw.replace("%", "").strip()
            try:
                vat = int(float(stawka_raw))
            except Exception:
                vat = 0
            brutto = round(netto * (1 + vat / 100), 2)
            pozycje.append({
                "nazwa": u.get("nazwa"),
                "ilosc": ilosc,
                "jednostka": "szt.",
                "netto": netto,
                "vat": vat,
                "brutto": brutto,
                "kategoria": u.get("kategoria"),
                "generated": True
            })

        return pozycje

    def _merge_positions(
            self,
            existing_positions: List[Dict[str, Any]],
            generated_positions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:

        result = [
            dict(p)
            for p in (existing_positions or [])
            if not (
                    p.get("generated")
                    and (p.get("nazwa") or "").strip().lower()
                    in self._NON_BILLABLE_TIME_LINES
            )
        ]

        for gp in generated_positions:
            gp_copy = dict(gp)
            gp_copy.setdefault("generated", True)

            gname = (gp_copy.get("nazwa") or "").strip()
            gnetto = round(self._to_float(gp_copy.get("netto")), 2)
            gvat = int(gp_copy.get("vat") or 0)
            gkat = (gp_copy.get("kategoria") or "").strip()

            generated_index = next(
                (
                    idx
                    for idx, existing in enumerate(result)
                    if existing.get("generated")
                       and (existing.get("nazwa") or "").strip() == gname
                       and round(
                    self._to_float(existing.get("netto")), 2
                ) == gnetto
                       and int(existing.get("vat") or 0) == gvat
                       and (existing.get("kategoria") or "").strip() == gkat
                ),
                None,
            )

            if generated_index is None:
                result.append(gp_copy)
            else:
                result[generated_index] = gp_copy

        return result

    # ---------------- SAVE SINGLE ----------------
    def generate_and_save_invoice(self, firma_id: int, miesiac: str) -> Dict[str, Any]:
        generated_positions = self.generate_invoice_positions(firma_id, miesiac)

        existing = get_invoice_for_company_month(firma_id, miesiac)
        if existing:
            try:
                existing_positions = json.loads(existing.get("pozycje_json") or "[]")
            except Exception:
                existing_positions = []

            merged = self._merge_positions(existing_positions, generated_positions)

            kwota_netto = round(sum(self._to_float(p.get("netto")) for p in merged), 2)
            kwota_brutto = round(sum(self._to_float(p.get("brutto")) for p in merged), 2)

            invoice_id = create_or_update_invoice(
                firma_id=firma_id,
                miesiac=miesiac,
                pozycje=merged,
                kwota_netto=kwota_netto,
                kwota_brutto=kwota_brutto,
                existing_invoice_id=existing["id"]
            )

            return {
                "id": invoice_id,
                "created": False,
                "firma_id": firma_id,
                "miesiac": miesiac,
                "pozycje": merged,
                "kwota_netto": kwota_netto,
                "kwota_brutto": kwota_brutto
            }
        else:
            for p in generated_positions:
                p.setdefault("generated", True)

            kwota_netto = round(sum(self._to_float(p.get("netto")) for p in generated_positions), 2)
            kwota_brutto = round(sum(self._to_float(p.get("brutto")) for p in generated_positions), 2)

            invoice_id = create_or_update_invoice(
                firma_id=firma_id,
                miesiac=miesiac,
                pozycje=generated_positions,
                kwota_netto=kwota_netto,
                kwota_brutto=kwota_brutto,
                existing_invoice_id=None
            )

            return {
                "id": invoice_id,
                "created": True,
                "firma_id": firma_id,
                "miesiac": miesiac,
                "pozycje": generated_positions,
                "kwota_netto": kwota_netto,
                "kwota_brutto": kwota_brutto
            }

    # ---------------- SAVE MONTH ----------------
    def generate_and_save_invoices_for_month(self, miesiac: str, only_active: bool = True) -> Dict[str, Any]:
        if not miesiac:
            return {"processed": 0, "created": 0, "updated": 0, "errors": ["Brak miesiąca."]}

        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if only_active:
            cur.execute("SELECT id FROM firmy WHERE status = 'Aktywne' OR status IS NULL")
        else:
            cur.execute("SELECT id FROM firmy")
        rows = cur.fetchall()
        conn.close()

        processed = 0
        created = 0
        updated = 0
        errors: List[str] = []

        for r in rows:
            fid = r["id"]

            try:
                company = self._get_company(fid)
                if not company:
                    continue

                fakturowac_flag = bool(company.get("fakturowac", 0))

                if fakturowac_flag:
                    # pełna faktura
                    result = self.generate_and_save_invoice(fid, miesiac)
                    processed += 1
                    if result.get("created"):
                        created += 1
                    else:
                        updated += 1

                else:
                    # tylko dodatkowe usługi + praca stoper
                    all_generated = self.generate_invoice_positions(fid, miesiac)

                    filtered = []
                    for p in all_generated:
                        name = (p.get("nazwa") or "").lower()
                        cat = (p.get("kategoria") or "").lower()

                        if cat == "księgowość" and name in (
                            "usługa księgowa",
                            "zdrowotna",
                            "dokumenty ponad limit",
                            "praca księgowa"
                        ):
                            continue

                        filtered.append(p)

                    if not filtered:
                        processed += 1
                        continue

                    existing = get_invoice_for_company_month(fid, miesiac)
                    if existing:
                        try:
                            existing_positions = json.loads(existing.get("pozycje_json") or "[]")
                        except Exception:
                            existing_positions = []

                        merged = self._merge_positions(existing_positions, filtered)
                        kw_net = round(sum(self._to_float(p.get("netto")) for p in merged), 2)
                        kw_br = round(sum(self._to_float(p.get("brutto")) for p in merged), 2)

                        invoice_id = create_or_update_invoice(
                            firma_id=fid,
                            miesiac=miesiac,
                            pozycje=merged,
                            kwota_netto=kw_net,
                            kwota_brutto=kw_br,
                            existing_invoice_id=existing["id"]
                        )

                        processed += 1
                        updated += 1

                    else:
                        for p in filtered:
                            p.setdefault("generated", True)
                        kw_net = round(sum(self._to_float(p.get("netto")) for p in filtered), 2)
                        kw_br = round(sum(self._to_float(p.get("brutto")) for p in filtered), 2)

                        invoice_id = create_or_update_invoice(
                            firma_id=fid,
                            miesiac=miesiac,
                            pozycje=filtered,
                            kwota_netto=kw_net,
                            kwota_brutto=kw_br,
                            existing_invoice_id=None
                        )

                        processed += 1
                        created += 1

            except Exception as e:
                errors.append(f"Firma {fid}: {e}")

        return {
            "processed": processed,
            "created": created,
            "updated": updated,
            "errors": errors
        }

    # ---------------- ADD SINGLE POSITION ----------------
    def add_single_position_to_invoice(self, firma_id: int, miesiac: str, position: Dict[str, Any], mark_generated: bool = True) -> Dict[str, Any]:
        existing = get_invoice_for_company_month(firma_id, miesiac)

        pos = dict(position)
        pos.setdefault("generated", bool(mark_generated))

        if existing:
            try:
                existing_positions = json.loads(existing.get("pozycje_json") or "[]")
            except Exception:
                existing_positions = []

            merged = self._merge_positions(existing_positions, [pos])
            kw_net = round(sum(self._to_float(p.get("netto")) for p in merged), 2)
            kw_br = round(sum(self._to_float(p.get("brutto")) for p in merged), 2)

            invoice_id = create_or_update_invoice(
                firma_id=firma_id,
                miesiac=miesiac,
                pozycje=merged,
                kwota_netto=kw_net,
                kwota_brutto=kw_br)