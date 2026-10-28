"""Budowanie bezpiecznych reguł folderów na podstawie historii IMAP.

Wiążemy *znane, już posortowane* wiadomości w folderach użytkownika
z adresami zewnętrznymi. Nie wymyślamy folderów na podstawie domeny.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from email.utils import getaddresses
from typing import Any, Dict, Iterable, List, Set, Tuple

from app.db.dao_conn import get_conn
from app.email.email_helpers import folder_role, decode_mailbox_utf7

logger = logging.getLogger(__name__)

# Serwisy publicznej poczty nie są automatycznie "domeną firmy" tylko
# dlatego, że biuro ma konto u dostawcy. Własne domeny można dodać w UI.
PUBLIC_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
    "live.com", "yahoo.com", "yahoo.pl", "wp.pl", "o2.pl", "onet.pl",
    "interia.pl", "icloud.com", "proton.me", "protonmail.com",
    "poczta.fm", "gazeta.pl",
})
MIN_EVIDENCE = 2


def _addresses(value: Any) -> Set[str]:
    """Poprawnie czyta 'Jan <jan@firma.pl>' i wielu odbiorców wiadomości."""
    values = getaddresses([str(value or "")])
    return {
        addr.strip().casefold()
        for _, addr in values
        if addr.count("@") == 1 and all(part for part in addr.split("@"))
    }


def _domain_set(configured: str, account_emails: Iterable[str]) -> Tuple[Set[str], Set[str]]:
    """Zwraca (domeny wewnętrzne, nasze dokładne adresy)."""
    own_addresses = set()
    own_domains = set()
    for value in account_emails:
        for address in _addresses(value):
            own_addresses.add(address)
            domain = address.rsplit("@", 1)[1]
            if domain not in PUBLIC_MAIL_DOMAINS:
                own_domains.add(domain)
    for token in (configured or "").replace(";", ",").split(","):
        domain = token.strip().casefold().lstrip("@")
        if domain and "." in domain and "@" not in domain and not any(x.isspace() for x in domain):
            own_domains.add(domain)
    return own_domains, own_addresses


def _internal(address: str, domains: Set[str], own: Set[str]) -> bool:
    domain = address.rsplit("@", 1)[-1]
    return address in own or domain in domains


def _eligible_folder(folder: Dict[str, Any]) -> bool:
    name = folder.get("server_name") or ""
    attrs = folder.get("attributes") or ""
    if not name or "\\Noselect".casefold() in attrs.casefold():
        return False
    return folder_role(name, attrs, folder.get("delimiter")) is None


def build_auto_filter_candidates(
    rows: Iterable[Dict[str, Any]],
    folder_info: Iterable[Dict[str, Any]],
    account_emails: Iterable[str],
    configured_domains: str = "",
    min_evidence: int = MIN_EVIDENCE,
) -> List[Dict[str, str]]:
    """Każdy email musi należeć jednoznacznie do jednego folderu.

    'Od' uczy się z nadawców. 'Do' uczy się z odbiorców tylko wiadomości
    wysłanych z naszego konta. Żadna domena wewnętrzna nie trafi do reguł.
    """
    own_domains, own_addresses = _domain_set(configured_domains, account_emails)
    valid_folders = {
        folder["server_name"] for folder in folder_info if _eligible_folder(folder)
    }
    frequency: Counter[Tuple[str, str]] = Counter()
    folders_by_address: Dict[str, Set[str]] = defaultdict(set)

    for row in rows:
        folder = row.get("server_name")
        if folder not in valid_folders:
            continue
        senders = _addresses(row.get("nadawca"))
        recipients = _addresses(row.get("odbiorca"))
        external = {
            address for address in senders
            if not _internal(address, own_domains, own_addresses)
        }
        # Nie uczymy się obcych odbiorców z korespondencji przychodzącej,
        # która często jest wysyłana do wielu firm w DW/Do.
        if senders and all(_internal(s, own_domains, own_addresses) for s in senders):
            external |= {
                address for address in recipients
                if not _internal(address, own_domains, own_addresses)
            }
        for address in external:
            frequency[(address, folder)] += 1
            folders_by_address[address].add(folder)

    candidates = []
    for (address, folder), count in sorted(frequency.items()):
        if count >= min_evidence and len(folders_by_address[address]) == 1:
            candidates.append({"address": address, "folder": folder})
    return candidates


def learn_filters_from_mail_history(konto_id: int, days: int = 730) -> Dict[str, int]:
    """Tworzy w PostgreSQL reguły 'od' i 'do' dla rozpoznanych kontaktów.

    Filtry zaczynają działać na kolejnych wiadomościach; późniejsze
    przeniesienie z INBOX jest wykonywane osobno, już po synchronizacji.
    """
    from app.db.dao_app_settings import get_setting

    conn = get_conn()
    try:
        account_rows = conn.execute(
            "SELECT email FROM konta_email WHERE email IS NOT NULL"
        ).fetchall()
        account_emails = [r[0] for r in account_rows]
        account = conn.execute(
            "SELECT pracownik_id FROM konta_email WHERE id = %s", (konto_id,)
        ).fetchone()
        if not account:
            raise ValueError("Nie znaleziono konta pocztowego.")
        owner_id = account[0]
        folders = [
            dict(row) for row in conn.execute(
                "SELECT server_name, attributes, delimiter FROM foldery_email WHERE konto_id = %s",
                (konto_id,),
            ).fetchall()
        ]
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = [
            dict(row) for row in conn.execute(
                """
                SELECT f.server_name, w.nadawca, w.odbiorca
                FROM wiadomosci_email w
                JOIN foldery_email f ON f.id = w.folder_id
                WHERE w.konto_id = %s AND COALESCE(w.usunieta, 0) = 0
                  AND w.data >= %s
                """,
                (konto_id, cutoff),
            ).fetchall()
        ]
        candidates = build_auto_filter_candidates(
            rows, folders, account_emails, get_setting("email_internal_domains") or "",
        )

        # Ręcznie ustawione filtry (również nieaktywne) mają pierwszeństwo.
        existing = set()
        for rule in conn.execute(
            'SELECT od, "do", nadawca_zawiera FROM reguly_filtrow WHERE konto_id = %s',
            (konto_id,),
        ).fetchall():
            for value in rule:
                existing |= _addresses(value)

        added = 0
        for candidate in candidates:
            email = candidate["address"]
            if email in existing:
                continue
            folder = candidate["folder"]
            for field in ("od", '"do"'):
                conn.execute(
                    f"""
                    INSERT INTO reguly_filtrow
                    (pracownik_id, konto_id, firma_id, {field},
                     folder_docelowy, aktywna, oznacz_jako_przeczytane, usun)
                    VALUES (%s, %s, NULL, %s, %s, 1, 0, 0)
                    """,
                    (owner_id, konto_id, email, folder),
                )
                added += 1
            existing.add(email)

        conn.commit()
        result = {"added": added, "addresses": added // 2, "candidates": len(candidates)}
        logger.info("Auto filtry konto=%s: %s", konto_id, result)
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
