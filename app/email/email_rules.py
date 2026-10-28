"""ReguĹ‚y filtrĂłw wiadomoĹ›ci: import z Thunderbirda (msgFilterRules JSON) i stosowanie do poczty."""
from __future__ import annotations

import logging
from email.utils import getaddresses
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from app.db.dao_conn import get_conn
from app.email.email_helpers import decode_mailbox_utf7, friendly_folder_name

logger = logging.getLogger(__name__)

# nsMsgSearchAttrib Thunderbirda -> pole reguĹ‚y
_ATTRIB_FIELDS = {0: "temat_zawiera", 1: "nadawca_zawiera", 6: "do", 8: "do"}
# nsMsgSearchOp: 0 zawiera, 2 jest, 9 zaczyna siÄ™ od - traktujemy jak "zawiera"
_SUPPORTED_OPS = {0, 2}
_ACTION_MOVE, _ACTION_DELETE, _ACTION_MARK_READ = 1, 3, 4
_FIELDS = ("od", "do", "nadawca_zawiera", "temat_zawiera", "tresc_zawiera")


def _folder_from_uri(uri: str, delimiter: Optional[str]) -> str:
    parts = uri.split("/", 3)
    path = unquote(parts[3] if len(parts) > 3 else "")
    if delimiter and delimiter != "/":
        path = path.replace("/", delimiter)
    return path


def _filter_terms(flt: Dict[str, Any]) -> Tuple[Optional[List[Dict[str, str]]], str]:
    """Zwraca listÄ™ reguĹ‚ (kaĹĽda to AND pĂłl) albo (None, powĂłd pominiÄ™cia)."""
    terms = flt.get("searchTerms") or []
    if not terms:
        return None, "brak warunkĂłw"
    parsed = []
    for term in terms:
        field = _ATTRIB_FIELDS.get(term.get("attrib"))
        value = ((term.get("value") or {}).get("str") or "").strip()
        if field is None or not value or term.get("arbitraryHeader"):
            return None, "nieobsĹ‚ugiwany warunek"
        if term.get("op", 0) not in _SUPPORTED_OPS:
            return None, "nieobsĹ‚ugiwany warunek"
        parsed.append((field, value, bool(term.get("booleanAnd"))))
    connectors = {and_ for _, _, and_ in parsed[1:]}
    if len(connectors) > 1:
        return None, "zĹ‚oĹĽony warunek (AND i OR razem)"
    if connectors == {False}:
        return [{field: value} for field, value, _ in parsed], ""
    rule: Dict[str, str] = {}
    for field, value, _ in parsed:
        if field in rule:
            return None, "dwa warunki na to samo pole"
        rule[field] = value
    return [rule], ""


def parse_thunderbird_filters(data: Any, delimiter: Optional[str] = ".") -> Tuple[List[Dict[str, Any]], List[str]]:
    """Konwertuje JSON filtrĂłw Thunderbirda na reguĹ‚y; zwraca (reguĹ‚y, pominiÄ™te z powodem)."""
    filters = data.get("filters", []) if isinstance(data, dict) else (data or [])
    rules: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for flt in filters:
        name = flt.get("filterName") or "(bez nazwy)"
        if flt.get("temporary"):
            skipped.append(f"{name}: filtr tymczasowy (np. spam)")
            continue
        folder, mark_read, delete = None, 0, False
        for action in flt.get("actionList") or []:
            kind = action.get("type")
            if kind == _ACTION_MOVE and action.get("targetFolderUri"):
                folder = _folder_from_uri(action["targetFolderUri"], delimiter)
            elif kind == _ACTION_MARK_READ:
                mark_read = 1
            elif kind == _ACTION_DELETE:
                delete = True
        if delete or not folder:
            skipped.append(f"{name}: brak akcji â€žprzenieĹ› do folderuâ€ť albo usuwanie")
            continue
        variants, reason = _filter_terms(flt)
        if variants is None:
            skipped.append(f"{name}: {reason}")
            continue
        for variant in variants:
            rules.append({
                "nazwa": name,
                "folder_docelowy": folder,
                "oznacz_jako_przeczytane": mark_read,
                "aktywna": 1 if flt.get("enabled", True) else 0,
                **{k: variant.get(k) for k in _FIELDS},
            })
    return rules, skipped


def import_thunderbird_filters(konto_id: int, data: Any) -> Tuple[int, int, List[str]]:
    """Zapisuje reguĹ‚y dla konta; zwraca (dodane, duplikaty, pominiÄ™te)."""
    conn = get_conn()
    try:
        delimiter = None
        row = conn.execute(
            "SELECT delimiter FROM foldery_email WHERE konto_id = %s AND delimiter IS NOT NULL LIMIT 1",
            (konto_id,),
        ).fetchone()
        if row:
            delimiter = row[0]
        rules, skipped = parse_thunderbird_filters(data, delimiter)
        owner = conn.execute("SELECT pracownik_id FROM konta_email WHERE id = %s", (konto_id,)).fetchone()
        owner_id = owner[0] if owner else None
        added = duplicates = 0
        for rule in rules:
            exists = conn.execute(
                """
                SELECT 1 FROM reguly_filtrow
                WHERE konto_id = %s AND firma_id IS NULL
                  AND COALESCE(od, '') = COALESCE(%s, '') AND COALESCE("do", '') = COALESCE(%s, '')
                  AND COALESCE(nadawca_zawiera, '') = COALESCE(%s, '')
                  AND COALESCE(temat_zawiera, '') = COALESCE(%s, '')
                  AND COALESCE(tresc_zawiera, '') = COALESCE(%s, '')
                  AND folder_docelowy = %s
                """,
                (konto_id, rule["od"], rule["do"], rule["nadawca_zawiera"], rule["temat_zawiera"],
                 rule["tresc_zawiera"], rule["folder_docelowy"]),
            ).fetchone()
            if exists:
                duplicates += 1
                continue
            conn.execute(
                """
                INSERT INTO reguly_filtrow (pracownik_id, konto_id, firma_id, od, "do", nadawca_zawiera,
                    temat_zawiera, tresc_zawiera, folder_docelowy, oznacz_jako_przeczytane, usun, aktywna)
                VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, 0, %s)
                """,
                (owner_id, konto_id, rule["od"], rule["do"], rule["nadawca_zawiera"], rule["temat_zawiera"],
                 rule["tresc_zawiera"], rule["folder_docelowy"], rule["oznacz_jako_przeczytane"],
                 rule["aktywna"]),
            )
            added += 1
        conn.commit()
        return added, duplicates, skipped
    finally:
        conn.close()


def _norm(value: Any) -> str:
    return (value or "").casefold()


def rule_matches(rule: Dict[str, Any], header: Dict[str, Any]) -> bool:
    """Wszystkie wypeĹ‚nione pola reguĹ‚y muszÄ… pasowaÄ‡ (reguĹ‚y z treĹ›ciÄ… wiadomoĹ›ci pomijamy)."""
    if _norm(rule.get("tresc_zawiera")):
        return False
    checks = (
        ("od", header.get("sender")),
        ("nadawca_zawiera", header.get("sender")),
        ("do", header.get("recipient")),
        ("temat_zawiera", header.get("subject")),
    )
    used = False
    for field, haystack in checks:
        needle = _norm(rule.get(field)).strip()
        if needle:
            used = True
            # Dla pełnych adresów w polach Od/Do wymagamy dokładnego
            # dopasowania adresu, nie fragmentu nazwy ani innego maila.
            if field in ("od", "do") and needle.count("@") == 1 and " " not in needle:
                addresses = {
                    email.strip().casefold()
                    for _, email in getaddresses([str(haystack or "")])
                }
                if needle not in addresses:
                    return False
            elif needle not in _norm(haystack):
                return False
    return used


def _active_rules(konto_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM reguly_filtrow
            WHERE konto_id = %s AND COALESCE(aktywna, 1) = 1 AND COALESCE(usun, 0) = 0
              AND COALESCE(folder_docelowy, '') <> ''
            ORDER BY id
            """,
            (konto_id,),
        ).fetchall()
        folders = conn.execute(
            "SELECT server_name, display_name FROM foldery_email WHERE konto_id = %s", (konto_id,)
        ).fetchall()
    finally:
        conn.close()
    by_name = {r["server_name"].casefold(): r["server_name"] for r in folders}
    by_display = {(r["display_name"] or "").casefold(): r["server_name"] for r in folders}
    by_friendly = {friendly_folder_name(r["server_name"]).casefold(): r["server_name"] for r in folders}
    rules = []
    for row in rows:
        rule = dict(row)
        wanted = rule["folder_docelowy"].casefold()
        target = (
            by_name.get(wanted)
            or by_display.get(decode_mailbox_utf7(rule["folder_docelowy"]).casefold())
            or by_friendly.get(friendly_folder_name(rule["folder_docelowy"]).casefold())
        )
        if target:
            rule["target"] = target
            rules.append(rule)
    return rules


def apply_rules(konto_id: int, folder: str, headers: List[Dict[str, Any]]) -> int:
    """Przenosi pasujÄ…ce wiadomoĹ›ci do folderĂłw z reguĹ‚ (pierwsza pasujÄ…ca reguĹ‚a wygrywa)."""
    from app.email.email_sync import mark_read_remote, move_emails

    rules = _active_rules(konto_id)
    if not rules or not headers:
        return 0
    moves: Dict[str, List[str]] = {}
    for header in headers:
        for rule in rules:
            if rule["target"] != folder and rule_matches(rule, header):
                if rule.get("oznacz_jako_przeczytane") and not header.get("is_seen"):
                    mark_read_remote(konto_id, folder, header["uid"])
                moves.setdefault(rule["target"], []).append(str(header["uid"]))
                break
    moved = 0
    for target, uids in moves.items():
        try:
            move_emails(konto_id, folder, uids, target)
            moved += len(uids)
        except Exception:
            logger.warning("Nie udaĹ‚o siÄ™ przenieĹ›Ä‡ wiadomoĹ›ci do %s", target, exc_info=True)
    return moved


def apply_rules_to_folder(konto_id: int, folder: str, history_days: Optional[int] = None) -> int:
    """Stosuje reguły do folderu; podczas importu tylko zadanego okresu."""
    from datetime import datetime, timedelta

    conn = get_conn()
    try:
        cutoff = (
            (datetime.now() - timedelta(days=history_days)).strftime("%Y-%m-%d")
            if history_days is not None else None
        )
        sql = """
            SELECT w.uid, w.nadawca, w.odbiorca, w.temat, w.przeczytana
            FROM wiadomosci_email w JOIN foldery_email f ON f.id = w.folder_id
            WHERE w.konto_id = %s AND f.server_name = %s AND COALESCE(w.usunieta, 0) = 0
        """
        params = [konto_id, folder]
        if cutoff is not None:
            sql += " AND w.data >= %s"
            params.append(cutoff)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    headers = [
        {"uid": r["uid"], "sender": r["nadawca"], "recipient": r["odbiorca"],
         "subject": r["temat"], "is_seen": bool(r["przeczytana"])}
        for r in rows
    ]
    return apply_rules(konto_id, folder, headers)
