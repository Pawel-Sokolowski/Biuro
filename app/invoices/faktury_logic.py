# app/invoices/faktury_logic.py
import json
import sqlite3
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.db.dao_conn import get_conn
from app.db.dao_faktury import (
    get_invoice_for_company_month,
    create_or_update_invoice
)


class FakturyLogic:

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

    # ---------------- USŁUGI DODATKOWE ----------------
    def _get_extra_services(self, firma_id: int, miesiac: str) -> List[dict]:
        conn = get_conn()
        conn.row_factory = sqlite3.Row
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
        if status and pricing and company.get("fakturowac", 0) == 1:
            ilosc_dok = int(status.get("ilosc_dok") or 0)
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

        # ---------------- Praca księgowa / kadrowa ----------------
        if status:
            sek_ks = int(status.get("sekundy_ksiegowosc") or 0)
            sek_kd = int(status.get("sekundy_kadry") or 0)

            if sek_ks > 0:
                godz = sek_ks / 3600
                netto = round(godz * 150, 2)
                brutto = round(netto * 1.23, 2)
                pozycje.append({
                    "nazwa": "Praca księgowa",
                    "ilosc": round(godz, 2),
                    "jednostka": "godz.",
                    "netto": netto,
                    "vat": 23,
                    "brutto": brutto,
                    "kategoria": "księgowość",
                    "generated": True
                })

            if sek_kd > 0:
                godz = sek_kd / 3600
                netto = round(godz * 120, 2)
                brutto = round(netto * 1.23, 2)
                pozycje.append({
                    "nazwa": "Praca kadrowa",
                    "ilosc": round(godz, 2),
                    "jednostka": "godz.",
                    "netto": netto,
                    "vat": 23,
                    "brutto": brutto,
                    "kategoria": "kadry",
                    "generated": True
                })

        return pozycje

    # ---------------- MERGE ----------------
    def _merge_positions(self, existing_positions: List[Dict[str, Any]], generated_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        existing = existing_positions or []
        name_map = {}
        for idx, p in enumerate(existing):
            name = (p.get("nazwa") or "").strip()
            if name:
                name_map.setdefault(name, []).append((idx, p))

        result = [dict(p) for p in existing]

        for gp in generated_positions:
            gname = (gp.get("nazwa") or "").strip()
            gp_copy = dict(gp)
            gp_copy.setdefault("generated", True)

            if not gname:
                result.append(gp_copy)
                continue

            found = name_map.get(gname)
            if found:
                replaced = False
                for idx, ex in found:
                    if ex.get("generated"):
                        result[idx] = gp_copy
                        replaced = True
                        break
                if not replaced:
                    result.append(gp_copy)
            else:
                result.append(gp_copy)

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