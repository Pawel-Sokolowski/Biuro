# app/chat_ui/chat_model.py
import sqlite3
from app.config import DB_NAME

def get_conversations(pracownik_id):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT k.id, k.nazwa
        FROM konwersacje k
        JOIN uczestnicy_chat uc ON k.id = uc.konwersacja_id
        WHERE uc.pracownik_id = ?
        ORDER BY k.data_utworzenia DESC
    """, (pracownik_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_messages(konwersacja_id, limit=100, offset=0):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT wc.id, p.imie, p.nazwisko, wc.tresc, wc.data_wysylki
        FROM wiadomosci_chat wc
        JOIN pracownicy p ON wc.pracownik_id = p.id
        WHERE wc.konwersacja_id = ?
        ORDER BY wc.data_wysylki DESC
        LIMIT ? OFFSET ?
    """, (konwersacja_id, limit, offset))
    rows = cur.fetchall()
    conn.close()
    return rows

def add_message(konwersacja_id, pracownik_id, tresc):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO wiadomosci_chat (konwersacja_id, pracownik_id, tresc, data_wysylki)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (konwersacja_id, pracownik_id, tresc))
    conn.commit()
    conn.close()

def edit_message(message_id, new_text):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        UPDATE wiadomosci_chat
        SET tresc = ?
        WHERE id = ?
    """, (new_text, message_id))
    conn.commit()
    conn.close()

def get_new_messages(konwersacja_id, last_timestamp):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT wc.id, p.imie, p.nazwisko, wc.tresc, wc.data_wysylki
        FROM wiadomosci_chat wc
        JOIN pracownicy p ON wc.pracownik_id = p.id
        WHERE wc.konwersacja_id = ?
        AND wc.data_wysylki > ?
        ORDER BY wc.data_wysylki ASC
    """, (konwersacja_id, last_timestamp))
    rows = cur.fetchall()
    conn.close()
    return rows
