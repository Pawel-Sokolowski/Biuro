# app/chat/chat_model.py
from typing import List, Dict, Any, Optional
from app.db.dao_conn import get_conn


def _get_conn():
    return get_conn()


def get_conversations(pracownik_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Zwraca listę konwersacji. Jeśli podano pracownik_id, można filtrować (jeśli istnieje tabela uczestnicy_chat).
    Aktualnie zwraca wszystkie konwersacje posortowane po dacie utworzenia malejąco.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT k.id, k.nazwa, k.typ, k.pracownik_creator_id,
                        k.data_utworzenia, k.ostatnia_aktualizacja
        FROM konwersacje k
        LEFT JOIN uczestnicy_chat uc ON uc.konwersacja_id = k.id
        WHERE %s::integer IS NULL
           OR k.pracownik_creator_id = %s
           OR uc.pracownik_id = %s
        ORDER BY data_utworzenia DESC
    """, (pracownik_id, pracownik_id, pracownik_id))
    rows = cur.fetchall()
    conn.close()
    return [{k: row[k] for k in row.keys()} for row in rows]


def get_messages(konwersacja_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Zwraca najnowsze wiadomości w kolejności naturalnej (najstarsze pierwsze).
    Offset jest liczony od końca rozmowy, aby przewijanie do góry mogło
    doładować starszą historię.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT wc.id, wc.konwersacja_id, wc.pracownik_id, wc.tresc, wc.data_wysylki,
               p.imie AS imie, p.nazwisko AS nazwisko
        FROM wiadomosci_chat wc
        LEFT JOIN pracownicy p ON wc.pracownik_id = p.id
        WHERE wc.konwersacja_id = %s
        ORDER BY wc.data_wysylki DESC, wc.id DESC
        LIMIT %s OFFSET %s
    """, (konwersacja_id, limit, offset))
    rows = cur.fetchall()
    conn.close()
    return [{k: row[k] for k in row.keys()} for row in reversed(rows)]


def add_message(konwersacja_id: int, pracownik_id: int, tresc: str) -> int:
    """
    Dodaje wiadomość do konwersacji. Zwraca id nowo dodanej wiadomości.
    Zakłada, że konwersacja_id jest poprawne (jeśli None, wywołujący powinien utworzyć konwersację).
    """
    if not is_participant(konwersacja_id, pracownik_id):
        raise PermissionError("Nie masz dostępu do tego kanału.")
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wiadomosci_chat (konwersacja_id, pracownik_id, tresc, data_wysylki)
        VALUES (%s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS')) RETURNING id
    """, (konwersacja_id, pracownik_id, tresc))
    _new_id = cur.fetchone()[0]
    cur.execute(
        "UPDATE konwersacje SET ostatnia_aktualizacja = to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id = %s",
        (konwersacja_id,),
    )
    conn.commit()
    last_id = _new_id
    conn.close()
    return last_id


def edit_message(message_id: int, new_text: str) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE wiadomosci_chat
        SET tresc = %s
        WHERE id = %s
    """, (new_text, message_id))
    conn.commit()
    conn.close()


def get_new_messages(konwersacja_id: int, last_timestamp: str) -> List[Dict[str, Any]]:
    """
    Pobiera wiadomości nowsze niż last_timestamp (format zgodny z zapisem w DB).
    Zwraca listę posortowaną rosnąco.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT wc.id, wc.konwersacja_id, wc.pracownik_id, wc.tresc, wc.data_wysylki,
               p.imie AS imie, p.nazwisko AS nazwisko
        FROM wiadomosci_chat wc
        LEFT JOIN pracownicy p ON wc.pracownik_id = p.id
        WHERE wc.konwersacja_id = %s
          AND wc.data_wysylki > %s
        ORDER BY wc.data_wysylki ASC, wc.id ASC
    """, (konwersacja_id, last_timestamp))
    rows = cur.fetchall()
    conn.close()
    return [{k: row[k] for k in row.keys()} for row in rows]


def create_conversation(nazwa: str, pracownik_creator_id: Optional[int] = None,
                        participant_ids: Optional[List[int]] = None) -> int:
    """
    Tworzy nową konwersację i zwraca jej id.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO konwersacje (nazwa, pracownik_creator_id)
        VALUES (%s, %s) RETURNING id
    """, (nazwa, pracownik_creator_id))
    _new_id = cur.fetchone()[0]
    last_id = _new_id
    ids = set(int(i) for i in (participant_ids or []))
    if pracownik_creator_id:
        ids.add(int(pracownik_creator_id))
    for pid in ids:
        cur.execute(
            "INSERT INTO uczestnicy_chat (konwersacja_id, pracownik_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (last_id, pid),
        )
    conn.commit()
    conn.close()
    return last_id


def get_conversation(konwersacja_id: int) -> Optional[Dict[str, Any]]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, nazwa, typ, pracownik_creator_id FROM konwersacje WHERE id = %s",
        (konwersacja_id,),
    ).fetchone()
    conn.close()
    return {k: row[k] for k in row.keys()} if row else None


def get_participant_ids(konwersacja_id: int) -> List[int]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT pracownik_id FROM uczestnicy_chat WHERE konwersacja_id = %s",
        (konwersacja_id,),
    ).fetchall()
    conn.close()
    return [r["pracownik_id"] for r in rows]


def is_participant(konwersacja_id: int, pracownik_id: Optional[int]) -> bool:
    if not pracownik_id:
        return False
    conn = _get_conn()
    row = conn.execute(
        """
        SELECT 1 FROM konwersacje k
        LEFT JOIN uczestnicy_chat uc
               ON uc.konwersacja_id = k.id AND uc.pracownik_id = %s
        WHERE k.id = %s AND (uc.id IS NOT NULL OR k.pracownik_creator_id = %s)
        """,
        (pracownik_id, konwersacja_id, pracownik_id),
    ).fetchone()
    conn.close()
    return row is not None


def can_manage_conversation(konwersacja_id: int, pracownik_id: Optional[int]) -> bool:
    """Zarządzać dostępem może właściciel kanału lub administrator."""
    if not pracownik_id:
        return False
    from app.security.authorization import is_administrator
    conv = get_conversation(konwersacja_id)
    if not conv:
        return False
    if conv.get("typ") == "prywatna":
        return False
    return conv["pracownik_creator_id"] == pracownik_id or is_administrator(pracownik_id)


def get_active_employees() -> List[Dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, imie, nazwisko, email FROM pracownicy "
        "WHERE COALESCE(aktywny, 1) = 1 ORDER BY nazwisko, imie"
    ).fetchall()
    conn.close()
    return [{k: r[k] for k in r.keys()} for r in rows]


def update_conversation(konwersacja_id: int, nazwa: str, participant_ids: List[int],
                        actor_id: Optional[int] = None) -> None:
    """Zmienia nazwę i listę uczestników. Właściciel kanału zawsze pozostaje uczestnikiem."""
    nazwa = (nazwa or "").strip()
    if not nazwa:
        raise ValueError("Nazwa kanału nie może być pusta.")
    if not can_manage_conversation(konwersacja_id, actor_id):
        raise PermissionError("Brak uprawnień do zarządzania dostępem do kanału.")
    conv = get_conversation(konwersacja_id)
    ids = set(int(i) for i in participant_ids)
    if conv["pracownik_creator_id"]:
        ids.add(int(conv["pracownik_creator_id"]))
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE konwersacje SET nazwa = %s WHERE id = %s", (nazwa, konwersacja_id))
    cur.execute("DELETE FROM uczestnicy_chat WHERE konwersacja_id = %s", (konwersacja_id,))
    for pid in ids:
        cur.execute(
            "INSERT INTO uczestnicy_chat (konwersacja_id, pracownik_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (konwersacja_id, pid),
        )
    conn.commit()
    conn.close()

def _direct_name(a: int, b: int) -> str:
    lo, hi = sorted((int(a), int(b)))
    return f"dm:{lo}-{hi}"


def ensure_direct_conversations(pracownik_id: int) -> None:
    """Tworzy brakujące rozmowy indywidualne z każdym innym aktywnym pracownikiem."""
    conn = _get_conn()
    cur = conn.cursor()
    existing = {r["nazwa"] for r in cur.execute(
        "SELECT nazwa FROM konwersacje WHERE typ = 'prywatna'").fetchall()}
    for emp in get_active_employees():
        other = emp["id"]
        if other == pracownik_id:
            continue
        name = _direct_name(pracownik_id, other)
        if name in existing:
            continue
        cur.execute(
            "INSERT INTO konwersacje (nazwa, typ, pracownik_creator_id) VALUES (%s, 'prywatna', %s) RETURNING id",
            (name, pracownik_id),
        )
        _new_id = cur.fetchone()[0]
        kid = _new_id
        for pid in (pracownik_id, other):
            cur.execute(
                "INSERT INTO uczestnicy_chat (konwersacja_id, pracownik_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (kid, pid),
            )
    conn.commit()
    conn.close()


def get_direct_conversations(pracownik_id: int) -> List[Dict[str, Any]]:
    """Rozmowy indywidualne pracownika wraz z danymi rozmówcy."""
    ensure_direct_conversations(pracownik_id)
    conn = _get_conn()
    rows = conn.execute(
        """
        SELECT k.id, k.nazwa, p.id AS partner_id, p.imie, p.nazwisko
        FROM konwersacje k
        JOIN uczestnicy_chat uc ON uc.konwersacja_id = k.id AND uc.pracownik_id = %s
        JOIN uczestnicy_chat uo ON uo.konwersacja_id = k.id AND uo.pracownik_id != %s
        JOIN pracownicy p ON p.id = uo.pracownik_id
        WHERE k.typ = 'prywatna' AND COALESCE(p.aktywny, 1) = 1
        ORDER BY p.nazwisko, p.imie
        """,
        (pracownik_id, pracownik_id),
    ).fetchall()
    conn.close()
    return [
        {"id": r["id"], "partner_id": r["partner_id"],
         "nazwa": f"{r['imie']} {r['nazwisko']}".strip()}
        for r in rows
    ]
