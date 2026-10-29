# app/invoices/faktury_logic.py
import json
import logging
import psycopg
from app.utils.parsing import to_float
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    InvoiceLockedError,
    get_invoice_for_company_month,
    create_or_update_invoice,
)
from app.db.dao_monthly import count_documents_for_month
from app.utils.months import previous_month


logger = logging.getLogger(__name__)


class FakturyLogic:
    _NON_BILLABLE_TIME_LINES = {"praca księgowa", "praca kadrowa"}

    # ---------------- STATUS MIESIĘCZNY ----------------
    def _get_month_status(self, firma_id: int, miesiac: str) -> Optional[dict]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM statusy_miesieczne
            WHERE firma_id = %s AND miesiac = %s
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
        cur = conn.cursor()
        cur.execute("""
            SELECT u.nazwa, u.kategoria, u.stawka_vat, uf.ilosc, uf.cena_netto,
                   uf.id AS extra_service_row_id, uf.usluga_id, uf.jednostka
            FROM uslugi_dodatkowe_firma uf
            JOIN uslugi_dodatkowe u ON uf.usluga_id = u.id
            WHERE uf.firma_id = %s AND uf.miesiac = %s AND uf.fakturowac = 1
            ORDER BY u.nazwa
        """, (firma_id, miesiac))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows] if rows else []

    # ---------------- FIRMA ----------------
    def _get_company(self, firma_id: int) -> Optional[dict]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM firmy WHERE id = %s", (firma_id,))
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
            WHERE klucz = %s
        """, (klucz,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    # ---------------- HELPER ----------------
    _to_float = staticmethod(to_float)

    @staticmethod
    def _accounting_vat() -> int:
        from app.db.dao_app_settings import get_setting

        raw = str(get_setting("ksiegowosc_domyslny_vat") or "23").replace("%", "").strip()
        try:
            return int(float(raw))
        except ValueError:
            return 23

    # ---------------- GENEROWANIE POZYCJI ----------------
    @staticmethod
    def _kadry_vat() -> int:
        from app.db.dao_app_settings import get_setting

        raw = str(get_setting("kadry_domyslny_vat") or "23").replace("%", "").strip()
        try:
            return int(float(raw))
        except ValueError:
            return 23

    def _kadry_positions(self, company: dict, stala_kwota: float) -> List[Dict[str, Any]]:
        vat = self._kadry_vat()
        mult = 1 + vat / 100

        def pos(nazwa, ilosc, netto_jedn):
            netto = round(netto_jedn * ilosc, 2)
            return {
                "nazwa": nazwa,
                "ilosc": ilosc,
                "jednostka": "mc" if ilosc == 1 and stala_kwota > 0 else "szt.",
                "netto": netto,
                "vat": vat,
                "brutto": round(netto * mult, 2),
                "kategoria": "kadry",
                "generated": True,
            }

        if stala_kwota > 0:
            return [pos("Usługa kadrowo-płacowa", 1, stala_kwota)]

        result: List[Dict[str, Any]] = []
        labels = {"uop": "Umowa o pracę", "uz": "Umowa zlecenie", "uod": "Umowa o dzieło"}
        conn = get_conn()
        try:
            cur = conn.cursor()
            for prefix, label in labels.items():
                ilosc = int(company.get(f"{prefix}_ilosc") or 0)
                klucz = company.get(f"{prefix}_cennik")
                if ilosc <= 0 or not klucz:
                    continue
                cur.execute(
                    "SELECT COALESCE(cena_netto_kadry, cena) AS cena_netto_kadry, opis "
                    "FROM cenniki_kadry WHERE klucz = %s",
                    (klucz,),
                )
                row = cur.fetchone()
                cena = self._to_float(row["cena_netto_kadry"]) if row else 0.0
                if cena > 0:
                    nazwa = str(row.get("opis") or "").strip() or f"Usługa kadrowo-płacowa - {label}"
                    result.append(pos(nazwa, ilosc, cena))
        finally:
            conn.close()
        return result

    def generate_invoice_positions(self, firma_id: int, miesiac: str) -> List[Dict[str, Any]]:
        company = self._get_company(firma_id)
        if not company:
            return []
        vat_rate = self._accounting_vat()
        vat_mult = 1 + vat_rate / 100

        status = self._get_month_status(firma_id, miesiac)
        extra = self._get_extra_services(firma_id, miesiac)

        pozycje: List[Dict[str, Any]] = []

        glowny = company.get("glowny_cennik")
        pricing = self._get_main_pricing(glowny) if glowny else None

        zdrowotna_val = 0.0
        wliczona = bool(company.get("wliczac_zdrowotna", 0))
        from app.db.dao_companies import count_zdrowotna_units
        zdrowotna_sztuk = count_zdrowotna_units(firma_id)
        if pricing:
            zdrowotna_val = self._to_float(company.get("zdrowotna", 0.0)) or \
                self._to_float(pricing.get("zdrowotna", 0.0))

        # ---------------- Usługa księgowa ----------------
        # Zakres fakturowania wynika z wypełnionych cenników: księgowość (cennik/stała kwota),
        # kadry (cennik/stała kwota); usługi dodatkowe są fakturowane zawsze.
        fakt_on = int(company.get("fakturowac") or 0) == 1
        ks_on = fakt_on
        kd_on = fakt_on
        stala_ks = self._to_float(company.get("stala_kwota_ksiegowosc"))
        stala_kd = self._to_float(company.get("stala_kwota_kadry"))
        if ks_on and stala_ks > 0:
            pozycje.append({
                "nazwa": "Usługa księgowa",
                "ilosc": 1,
                "jednostka": "mc",
                "netto": round(stala_ks, 2),
                "vat": vat_rate,
                "brutto": round(stala_ks * vat_mult, 2),
                "kategoria": "księgowość",
                "generated": True
            })
            ks_full = False
        else:
            ks_full = ks_on and bool(pricing)

        if ks_full:
            netto = self._to_float(company.get("cena_netto_ksiegowosc", 0.0)) or \
                self._to_float(pricing.get("cena_netto_ksiegowosc", 0.0))
            brutto = round(netto * vat_mult, 2)
            if wliczona and zdrowotna_val > 0:
                netto = round(netto + zdrowotna_val * zdrowotna_sztuk, 2)
                brutto = round(brutto + zdrowotna_val * zdrowotna_sztuk * vat_mult, 2)
            pozycje.append({
                "nazwa": "Usługa księgowa",
                "ilosc": 1,
                "jednostka": "mc",
                "netto": netto,
                "vat": vat_rate,
                "brutto": brutto,
                "kategoria": "księgowość",
                "generated": True
            })

        # ---------------- Dokumenty ponad limit ----------------
        if ks_full:
            ilosc_dok = self._get_document_count_for_invoice(
                firma_id, miesiac, int((status or {}).get("ilosc_dok") or 0)
            )
            limit = int(company.get("limit_dokumentow") or 0)
            if ilosc_dok > limit:
                ponad = ilosc_dok - limit
                cena = self._to_float(company.get("cena_ponad_limit", 0.0)) or \
                    self._to_float(pricing.get("cena_za_dokument_ponad_limit", 0.0))
                netto = round(ponad * cena, 2)
                brutto = round(netto * vat_mult, 2)
                pozycje.append({
                    "nazwa": "Dokumenty ponad limit",
                    "ilosc": ponad,
                    "jednostka": "szt.",
                    "netto": netto,
                    "vat": vat_rate,
                    "brutto": brutto,
                    "kategoria": "księgowość",
                    "generated": True
                })

        # ---------------- Zdrowotna ----------------
        if ks_full and not wliczona:
            if zdrowotna_val > 0:
                sztuk = zdrowotna_sztuk
                pozycje.append({
                    "nazwa": "Zdrowotna",
                    "ilosc": sztuk,
                    "jednostka": "szt.",
                    "netto": round(zdrowotna_val * sztuk, 2),
                    "vat": vat_rate,
                    "brutto": round(zdrowotna_val * sztuk * vat_mult, 2),
                    "kategoria": "księgowość",
                    "generated": True
                })

        # ---------------- Kadry ----------------
        if kd_on:
            pozycje.extend(self._kadry_positions(company, stala_kd))

        # ---------------- Usługi dodatkowe (zawsze) ----------------
        for u in extra:
            ilosc = float(u.get("ilosc") or 0)
            if ilosc <= 0:
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
                "jednostka": u.get("jednostka") or "szt.",
                "netto": netto,
                "cena_netto": cena_netto,
                "vat": vat,
                "brutto": brutto,
                "kategoria": u.get("kategoria"),
                "usluga_id": u.get("usluga_id"),
                "extra_service_row_id": u.get("extra_service_row_id"),
                "generated": True
            })

        return pozycje

    def _merge_positions(
            self,
            existing_positions: List[Dict[str, Any]],
            generated_positions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Odtwarza pozycje automatyczne i zachowuje ręczne bez dublowania usług.

        Usługi z ewidencji dodatkowej mogą być już widoczne jako ręcznie dodana
        pozycja na fakturze. Zastępujemy wówczas tę kopię wersją z ewidencji.
        """
        from collections import Counter

        generated = []
        for position in generated_positions:
            item = dict(position)
            item.setdefault("generated", True)
            generated.append(item)

        def signature(position):
            service_id = position.get("usluga_id")
            if not service_id:
                return None
            try:
                return (
                    int(service_id),
                    round(float(position.get("ilosc") or 0), 2),
                    round(self._to_float(position.get("netto")), 2),
                )
            except (ValueError, TypeError):
                return None

        generated_services = Counter(
            sig for position in generated
            if position.get("extra_service_row_id")
            for sig in [signature(position)] if sig is not None
        )
        generated_row_ids = {
            position["extra_service_row_id"]
            for position in generated if position.get("extra_service_row_id")
        }
        retained = []
        for position in existing_positions or []:
            if position.get("generated"):
                # Zastąpione aktualnymi cennikami i ewidencją usług.
                continue
            if position.get("extra_service_row_id") in generated_row_ids:
                sig = signature(position)
                if sig is not None and generated_services[sig] > 0:
                    generated_services[sig] -= 1
                continue
            sig = signature(position)
            if sig is not None and generated_services[sig] > 0:
                generated_services[sig] -= 1
                continue
            retained.append(dict(position))
        return retained + generated

    # ---------------- SAVE SINGLE ----------------
    def generate_and_save_invoice(self, firma_id: int, miesiac: str) -> Dict[str, Any]:
        generated_positions = self.generate_invoice_positions(firma_id, miesiac)

        existing = get_invoice_for_company_month(firma_id, miesiac)
        if not existing and not generated_positions:
            return {"id": None, "created": False, "skipped": True, "firma_id": firma_id,
                    "miesiac": miesiac, "pozycje": [], "kwota_netto": 0.0, "kwota_brutto": 0.0}
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
        skipped = 0
        skipped_details: List[str] = []
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
                    if result.get("skipped"):
                        skipped += 1
                        skipped_details.append(self._skip_description(fid, miesiac))
                        continue
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

            except InvoiceLockedError:
                skipped += 1
                skipped_details.append(self._skip_description(fid, miesiac))
            except Exception as e:
                logger.exception("Błąd generowania faktury dla firmy %s (%s)", fid, miesiac)
                errors.append(f"Firma {fid}: {e}")

        return {
            "processed": processed,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "skipped_details": skipped_details,
            "errors": errors
        }

    def _skip_description(self, firma_id: int, miesiac: str, reason: Optional[str] = None) -> str:
        company = self._get_company(firma_id) or {}
        name = company.get("nazwa") or company.get("nazwa_skrocona") or f"Firma {firma_id}"
        if reason:
            return f"{name} - {reason}"
        try:
            existing = get_invoice_for_company_month(firma_id, miesiac) or {}
        except Exception:
            existing = {}
        if not existing:
            return f"{name} - brak pozycji do zafakturowania"
        numer = existing.get("numer")
        suffix = f" ({numer})" if numer else ""
        hint = "usuń ją przyciskiem „Usuń wystawioną” i wygeneruj ponownie, aby dodać usługi dodatkowe"
        if not company.get("fakturowac", 0):
            return f"{name} - płatność z góry za rok{suffix}; {hint}"
        return f"{name} - faktura już wystawiona{suffix}; {hint}"

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
                kwota_brutto=kw_br,
                existing_invoice_id=existing["id"])