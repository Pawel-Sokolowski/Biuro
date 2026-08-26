# app/chat/chat_model.py
import sqlite3
from typing import List, Dict, Any, Optional
from app.config import DB_NAME


def _get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def get_conversations(pracownik_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Zwraca listę konwersacji. Jeśli podano pracownik_id, można filtrować (jeśli istnieje tabela uczestnicy_chat).
    Aktualnie zwraca wszystkie konwersacje posortowane po dacie utworzenia malejąco.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, nazwa, pracownik_creator_id, data_utworzenia, ostatnia_aktualizacja
        FROM konwersacje
        ORDER BY datetime(data_utworzenia) DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return [{k: row[k] for k in row.keys()} for row in rows]


def get_messages(konwersacja_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Zwraca wiadomości dla konwersacji posortowane rosnąco (najstarsze pierwsze),
    co ułatwia grupowanie i renderowanie w kolejności naturalnej.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT wc.id, wc.konwersacja_id, wc.pracownik_id, wc.tresc, wc.data_wysylki,
               p.imie AS imie, p.nazwisko AS nazwisko
        FROM wiadomosci_chat wc
        LEFT JOIN pracownicy p ON wc.pracownik_id = p.id
        WHERE wc.konwersacja_id = ?
        ORDER BY datetime(wc.data_wysylki) ASC, wc.id ASC
        LIMIT ? OFFSET ?
    """, (konwersacja_id, limit, offset))
    rows = cur.fetchall()
    conn.close()
    return [{k: row[k] for k in row.keys()} for row in rows]


def add_message(konwersacja_id: int, pracownik_id: int, tresc: str) -> int:
    """
    Dodaje wiadomość do konwersacji. Zwraca id nowo dodanej wiadomości.
    Zakłada, że konwersacja_id jest poprawne (jeśli None, wywołujący powinien utworzyć konwersację).
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wiadomosci_chat (konwersacja_id, pracownik_id, tresc, data_wysylki)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (konwersacja_id, pracownik_id, tresc))
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


def edit_message(message_id: int, new_text: str) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE wiadomosci_chat
        SET tresc = ?
        WHERE id = ?
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
        WHERE wc.konwersacja_id = ?
          AND datetime(wc.data_wysylki) > datetime(?)
        ORDER BY datetime(wc.data_wysylki) ASC, wc.id ASC
    """, (konwersacja_id, last_timestamp))
    rows = cur.fetchall()
    conn.close()
    return [{k: row[k] for k in row.keys()} for row in rows]


def create_conversation(nazwa: str, pracownik_creator_id: Optional[int] = None) -> int:
    """
    Tworzy nową konwersację i zwraca jej id.
    """
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO konwersacje (nazwa, pracownik_creator_id)
        VALUES (?, ?)
    """, (nazwa, pracownik_creator_id))
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id
