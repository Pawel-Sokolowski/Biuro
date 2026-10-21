"""Aktualizacja schematu bazy danych. Uruchom po każdej aktualizacji programu:

    python migrate_db.py

Najlepiej, gdy inni użytkownicy nie są w tym czasie zalogowani.
"""
import sys

from app.db.schema import init_db, schema_is_current


def main() -> int:
    print("Aktualizacja bazy danych...")
    init_db()
    if schema_is_current():
        print("Gotowe - baza jest aktualna.")
        return 0
    print("Błąd: baza nie została oznaczona jako aktualna.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
