# app/invoices/faktury_logic.py
import sqlite3
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.db.dao_conn import get_conn
from app.db.dao_faktury import (
    get_invoice_for_company_month,
    create_or_update_invoice
)
from app.db.dao_companies import get_company


class FakturyLogic:
    """
    Warstwa biznesowa generowania faktur:
      - generowanie pozycji (ryczałt, ponad limit, zdrowotna, usługi dodatkowe, stoper)
      - idempotentne scalanie pozycji (nadpisywanie wygenerowanych, zachowanie ręcznych)
      - koordynacja zapisu przez DAO (create_or_update_invoice)
      - pomocnicze funkcje do dodawania pojedynczych pozycji i walidacji
    """

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

    def _get_company(self, firma_id: int) -> Optional[sqlite3.Row]:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, nazwa, nip, miasto, glowny_cennik, limit_dokumentow,
                   typ_rozliczenia, vat_czynny, limit_kadry, limit_ryczalt, zdrowotna
            FROM firmy
            WHERE id = ?
        """, (firma_id,))
        row = cur.fetchone()
        conn.close()
        return row

    def _get_main_pricing(self, klucz: str) -> Optional[sqlite3.Row]:
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
            WHERE klucz = ?
        """, (klucz,))
        row = cur.fetchone()
        conn.close()
        return row

    def _to_float(self, v, default: float = 0.0) -> float:
        try:
            if v is None or v == "":
                return float(default)
            return float(v)
        except Exception:
            return float(default)

    # -------------------------
    # Generowanie pozycji
    # -------------------------
    def generate_invoice_positions(self, firma_id: int, miesiac: str) -> List[Dict[str, Any]]:
        """
        Generuje pozycje faktury dla pojedynczej firmy i miesiąca.
        Każda pozycja generowana przez system otrzymuje pole "generated": True.
        """
        company = self._get_company(firma_id)
        if not company:
            return []

        status = self._get_month_status(firma_id, miesiac)
        extra = self._get_extra_services(firma_id, miesiac)

        pozycje: List[Dict[str, Any]] = []

        # 1. Usługa księgowa (ryczałt / główny cennik)
        glowny = company["glowny_cennik"] if "glowny_cennik" in company.keys() else None
        if glowny:
            pricing = self._get_main_pricing(glowny)
            if pricing:
                netto_val = pricing["cena_netto_ksiegowosc"] if pricing["cena_netto_ksiegowosc"] is not None else pricing["netto"]
                netto = self._to_float(netto_val, 0.0)
                vat = 23
                brutto = round(netto * 1.23, 2)

                pozycje.append({
                    "nazwa": "Usługa księgowa",
                    "ilosc": 1,
                    "jednostka": "mc",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "księgowość",
                    "generated": True
                })

        # 2. Dokumenty ponad limit
        if status:
            ilosc_dok = status["ilosc_dok"] or 0
            limit = company["limit_dokumentow"] or 0

            if ilosc_dok > limit:
                ponad = ilosc_dok - limit
                pricing = self._get_main_pricing(glowny) if glowny else None
                if pricing:
                    cena_val = pricing["cena_za_dokument_ponad_limit"] if pricing["cena_za_dokument_ponad_limit"] is not None else pricing["netto"]
                    cena = self._to_float(cena_val, 0.0)
                    netto = round(ponad * cena, 2)
                    vat = 23
                    brutto = round(netto * 1.23, 2)

                    pozycje.append({
                        "nazwa": "Dokumenty ponad limit",
                        "ilosc": ponad,
                        "jednostka": "szt.",
                        "netto": netto,
                        "vat": vat,
                        "brutto": brutto,
                        "kategoria": "księgowość",
                        "generated": True
                    })

        # 3. Zdrowotna
        if glowny:
            pricing = self._get_main_pricing(glowny)
            if pricing:
                zdrowotna_val = pricing.get("zdrowotna") if isinstance(pricing, dict) else pricing["zdrowotna"]
                try:
                    zdrowotna_num = float(zdrowotna_val) if zdrowotna_val is not None else 0.0
                except Exception:
                    zdrowotna_num = 0.0

                if zdrowotna_num > 0:
                    netto = zdrowotna_num
                    vat = 0
                    brutto = netto

                    pozycje.append({
                        "nazwa": "Zdrowotna",
                        "ilosc": 1,
                        "jednostka": "mc",
                        "netto": netto,
                        "vat": vat,
                        "brutto": brutto,
                        "kategoria": "księgowość",
                        "generated": True
                    })

        # 4. Usługi dodatkowe
        for u in extra:
            ilosc = u["ilosc"] or 0
            if ilosc == 0:
                continue

            try:
                cena_netto = float(u["cena_netto"] or 0)
            except Exception:
                cena_netto = 0.0

            netto = round(cena_netto * ilosc, 2)

            stawka_raw = u.get("stawka_vat") or "0"
            if isinstance(stawka_raw, str):
                stawka_raw = stawka_raw.replace("%", "").strip()
            try:
                vat = int(stawka_raw)
            except Exception:
                try:
                    vat = int(float(stawka_raw))
                except Exception:
                    vat = 0

            brutto = round(netto * (1 + vat / 100), 2)

            pozycje.append({
                "nazwa": u["nazwa"],
                "ilosc": ilosc,
                "jednostka": "szt.",
                "netto": netto,
                "vat": vat,
                "brutto": brutto,
                "kategoria": u["kategoria"],
                "generated": True
            })

        # 5. Stoper księgowość / kadry
        if status:
            sek_ks = status.get("sekundy_ksiegowosc") or 0
            sek_kd = status.get("sekundy_kadry") or 0

            if sek_ks > 0:
                godz = sek_ks / 3600
                netto = round(godz * 150, 2)
                vat = 23
                brutto = round(netto * 1.23, 2)

                pozycje.append({
                    "nazwa": "Praca księgowa",
                    "ilosc": round(godz, 2),
                    "jednostka": "godz.",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "księgowość",
                    "generated": True
                })

            if sek_kd > 0:
                godz = sek_kd / 3600
                netto = round(godz * 120, 2)
                vat = 23
                brutto = round(netto * 1.23, 2)

                pozycje.append({
                    "nazwa": "Praca kadrowa",
                    "ilosc": round(godz, 2),
                    "jednostka": "godz.",
                    "netto": netto,
                    "vat": vat,
                    "brutto": brutto,
                    "kategoria": "kadry",
                    "generated": True
                })

        return pozycje

    # -------------------------
    # Scalanie pozycji (idempotencja)
    # -------------------------
    def _merge_positions(self, existing_positions: List[Dict[str, Any]], generated_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scalanie pozycji:
        - istniejące pozycje z 'generated': True zostaną nadpisane przez odpowiadające generated_positions (po nazwie)
        - istniejące pozycje bez 'generated' (ręczne) pozostają bez zmian
        - nowe generated_positions, których nie ma w existing, zostaną dodane
        - porównanie po 'nazwa' (można rozszerzyć o kategorię/jednostkę jeśli potrzeba)
        """
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

    # -------------------------
    # Koordynacja zapisu
    # -------------------------
    def generate_and_save_invoice(self, firma_id: int, miesiac: str) -> Dict[str, Any]:
        """
        Generuje pozycje i zapisuje fakturę. Jeśli faktura istnieje:
         - scala istniejące pozycje z nowo wygenerowanymi (nadpisuje wygenerowane)
         - nie duplikuje pozycji wygenerowanych wcześniej
        Zwraca dict z informacją o id, created (True jeśli nowa), oraz sumach.
        """
        generated_positions = self.generate_invoice_positions(firma_id, miesiac)

        gen_netto = sum(float(p.get("netto", 0) or 0) for p in generated_positions)
        gen_brutto = sum(float(p.get("brutto", 0) or 0) for p in generated_positions)

        existing = get_invoice_for_company_month(firma_id, miesiac)
        if existing:
            existing_positions = []
            try:
                existing_positions = json.loads(existing.get("pozycje_json") or "[]")
            except Exception:
                existing_positions = []

            merged = self._merge_positions(existing_positions, generated_positions)

            kwota_netto = round(sum(float(p.get("netto", 0) or 0) for p in merged), 2)
            kwota_brutto = round(sum(float(p.get("brutto", 0) or 0) for p in merged), 2)

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

            kwota_netto = round(sum(float(p.get("netto", 0) or 0) for p in generated_positions), 2)
            kwota_brutto = round(sum(float(p.get("brutto", 0) or 0) for p in generated_positions), 2)

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

    def generate_and_save_invoices_for_month(self, miesiac: str, only_active: bool = True) -> Dict[str, Any]:
        """
        Generuje i zapisuje faktury dla wszystkich firm za podany miesiąc.
        Zwraca podsumowanie: {'processed': int, 'created': int, 'updated': int, 'errors': [str,...]}
        """
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
        errors: List[str] = []

        for r in rows:
            fid = r["id"]
            try:
                result = self.generate_and_save_invoice(fid, miesiac)
                processed += 1
                if result.get("created"):
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append(f"Firma {fid}: {e}")

        return {
            "processed": processed,
            "created": created,
            "updated": updated,
            "errors": errors
        }

    # -------------------------
    # Dodatkowe pomocnicze operacje
    # -------------------------
    def add_single_position_to_invoice(self, firma_id: int, miesiac: str, position: Dict[str, Any], mark_generated: bool = True) -> Dict[str, Any]:
        """
        Dodaje pojedynczą pozycję do faktury (tworzy fakturę jeśli nie istnieje).
        - mark_generated: jeśli True, pozycja zostanie oznaczona jako wygenerowana (generated=True)
        - jeśli istnieje wygenerowana pozycja o tej samej nazwie, zostanie nadpisana
        - jeśli istnieje ręczna pozycja o tej samej nazwie, nowa pozycja zostanie dodana
        """
        existing = get_invoice_for_company_month(firma_id, miesiac)

        pos = dict(position)
        pos.setdefault("generated", bool(mark_generated))

        if existing:
            try:
                existing_positions = json.loads(existing.get("pozycje_json") or "[]")
            except Exception:
                existing_positions = []

            merged = self._merge_positions(existing_positions, [pos])
            kw_net = round(sum(float(p.get("netto", 0) or 0) for p in merged), 2)
            kw_br = round(sum(float(p.get("brutto", 0) or 0) for p in merged), 2)

            invoice_id = create_or_update_invoice(
                firma_id=firma_id,
                miesiac=miesiac,
                pozycje=merged,
                kwota_netto=kw_net,
                kwota_brutto=kw_br,
                existing_invoice_id=existing["id"]
            )

            return {"id": invoice_id, "created": False, "pozycje": merged, "kwota_netto": kw_net, "kwota_brutto": kw_br}
        else:
            kw_net = round(float(pos.get("netto", 0) or 0), 2)
            kw_br = round(float(pos.get("brutto", 0) or 0), 2)

            invoice_id = create_or_update_invoice(
                firma_id=firma_id,
                miesiac=miesiac,
                pozycje=[pos],
                kwota_netto=kw_net,
                kwota_brutto=kw_br,
                existing_invoice_id=None
            )

            return {"id": invoice_id, "created": True, "pozycje": [pos], "kwota_netto": kw_net, "kwota_brutto": kw_br}
