"""Bezpieczne, ręcznie uruchamiane uzupełnianie danych firm z rejestrów.

Nie modyfikuje cen, kadrowych ilości umów, statusu obsługi ani danych miesięcznych.
Każda firma jest zapisywana w osobnej transakcji, po powtórnym odczycie z bazy.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from app.companies.address_parsing import split_street_address
from app.companies.company_lookup import lookup_company_by_nip, _kind
from app.companies.company_names import sole_proprietor_short_name
from app.db.dao_conn import get_conn


# Wąska lista dozwolonych kolumn, wszystkie są tekstowe w tabeli firmy.
# Adres zamieszkania NIE jest wypełniany z danych wspólników CEIDG.
_REGISTRY_TEXT_FIELDS = {
    "nazwa": "nazwa",
    "regon": "regon",
    "krs": "krs",
    "dz_miasto": "miasto",
    "dz_kod": "kod_pocztowy",
    "dz_ulica": "ulica",
    "dz_budynek": "adres_budynek",
    "dz_lokal": "adres_lokal",
    "dz_wojewodztwo": "wojewodztwo",
    "dz_powiat": "powiat",
    "dz_gmina": "gmina",
    "dz_kraj": "kraj",
    "ko_miasto": "adres_koresp_miasto",
    "ko_kod": "adres_koresp_kod_pocztowy",
    "ko_ulica": "adres_koresp_ulica",
    "ko_budynek": "adres_koresp_budynek",
    "ko_lokal": "adres_koresp_lokal",
    "ko_wojewodztwo": "adres_koresp_wojewodztwo",
    "ko_powiat": "adres_koresp_powiat",
    "ko_gmina": "adres_koresp_gmina",
    "ko_kraj": "adres_koresp_kraj",
    "data_rozpoczecia_dg": "data_rozpoczecia_dg",
    "kody_pkd": "kody_pkd",
    "adres_doreczen_elektronicznych": "adres_doreczen_elektronicznych",
    "email": "adres_email",
    "telefon": "nr_telefonu",
    "konto_bankowe": "konto_bankowe",
}


def _missing(value: Any) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _value(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item).strip() for item in value if item)
    text = str(value).strip() if value is not None else ""
    return text or None


def build_missing_registry_updates(
    current: Dict[str, Any], registry: Dict[str, Any]
) -> Dict[str, Any]:
    """Tylko brakujące dane; aktualne wartości z DB zawsze mają pierwszeństwo."""
    if not registry or registry.get("error"):
        return {}

    incoming = dict(registry)
    # GUS/KRS/CEIDG wystawiają różne aliasy pól.
    for prefix in ("dz", "ko"):
        if prefix == "ko":
            for part in ("miasto", "kod", "ulica", "budynek", "lokal",
                         "wojewodztwo", "powiat", "gmina", "kraj"):
                incoming.setdefault(f"ko_{part}", incoming.get(f"koresp_{part}"))
        street, building, apartment = split_street_address(
            incoming.get(f"{prefix}_ulica"),
            incoming.get(f"{prefix}_budynek"),
            incoming.get(f"{prefix}_lokal"),
        )
        incoming[f"{prefix}_ulica"] = street
        incoming[f"{prefix}_budynek"] = building
        incoming[f"{prefix}_lokal"] = apartment

    incoming.setdefault("dz_miasto", incoming.get("miasto"))
    incoming.setdefault("dz_kod", incoming.get("kod_pocztowy"))
    incoming.setdefault("dz_kraj", incoming.get("kraj"))
    incoming.setdefault("data_rozpoczecia_dg", incoming.get("data_rozpoczecia"))
    incoming.setdefault("kody_pkd", incoming.get("pkd"))

    # Z CEIDG można uzupełnić nazwę skróconą przedsiębiorcy, ale nigdy
    # nie tworzymy jej na podstawie listy wspólników spółki cywilnej.
    updates: Dict[str, Any] = {}
    if _kind(current.get("forma_dzialalnosci")) == "jdg":
        short = sole_proprietor_short_name(incoming.get("imie"), incoming.get("nazwisko"))
        if short and _missing(current.get("nazwa_skrocona")):
            updates["nazwa_skrocona"] = short

    for source, column in _REGISTRY_TEXT_FIELDS.items():
        if column == "krs" and _kind(current.get("forma_dzialalnosci")) in ("jdg", "civil"):
            continue
        value = _value(incoming.get(source))
        if value is None or not _missing(current.get(column)):
            continue
        updates[column] = value

    # Status VAT sprawdzamy dla WSZYSTKICH, lecz nie nadpisujemy znanego
    # już statusu; "Brak danych" oznacza pole wymagające uzupełnienia.
    vat_status = _value(incoming.get("status_vat"))
    existing_vat = _value(current.get("status_vat"))
    if (vat_status in ("Czynny", "Zwolniony")
            and (not existing_vat or existing_vat == "Brak danych")):
        updates["status_vat"] = vat_status
        # vat_czynny to pochodna statusu VAT; wartość DEFAULT 0 nie
        # rozróżnia "nieznany" od "zwolniony".
        updates["vat_czynny"] = 1 if vat_status == "Czynny" else 0
    return updates


def enrich_all_companies(
    progress: Optional[Callable[[int, int, str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    lookup: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Przetwarza każdy zapisany NIP; pozwala przerwać przed następną firmą."""
    lookup = lookup or lookup_company_by_nip

    conn = get_conn()
    try:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT id, nip, nazwa, forma_dzialalnosci FROM firmy ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()

    result = {
        "total": len(rows), "processed": 0, "updated": 0,
        "updated_fields": 0, "unchanged": 0, "skipped": 0,
        "errors": [], "cancelled": False,
    }
    for company in rows:
        if should_cancel and should_cancel():
            result["cancelled"] = True
            break

        company_id = company["id"]
        name = str(company.get("nazwa") or f"ID {company_id}")
        nip = "".join(ch for ch in str(company.get("nip") or "") if ch.isdigit())
        try:
            if len(nip) != 10:
                result["skipped"] += 1
            else:
                registry = lookup(nip, forma_dzialalnosci=company.get("forma_dzialalnosci")) or {}
                if registry.get("error"):
                    raise ValueError(registry.get("message") or "Błąd rejestru.")
                returned_nip = "".join(ch for ch in str(registry.get("nip") or "") if ch.isdigit())
                if returned_nip and returned_nip != nip:
                    raise ValueError("Rejestr zwrócił dane innego NIP.")

                # Nowa transakcja i blokada wyłącznie na czas krótkiego zapisu.
                # Chroni edycje wykonane przez innych pracowników w trakcie pobierania.
                conn = get_conn()
                try:
                    row = conn.execute(
                        "SELECT * FROM firmy WHERE id = %s FOR UPDATE", (company_id,)
                    ).fetchone()
                    updates = build_missing_registry_updates(dict(row), registry) if row else {}
                    if updates:
                        fields = list(updates)
                        conn.execute(
                            "UPDATE firmy SET " + ", ".join(f"{field} = %s" for field in fields)
                            + " WHERE id = %s",
                            [updates[field] for field in fields] + [company_id],
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()

                if updates:
                    result["updated"] += 1
                    result["updated_fields"] += len(updates)
                else:
                    result["unchanged"] += 1
        except Exception as exc:
            result["errors"].append(f"{name} (ID {company_id}): {exc}")
        finally:
            result["processed"] += 1
            if progress:
                progress(result["processed"], result["total"], name)
    return result
