"""Usuwa dane firm (klienci, cenniki, faktury, dokumenty, statusy, importy). Zostaj? pracownicy, maile, ustawienia.

Bez parametr?w tylko pokazuje podgl?d. Usuni?cie: python reset_company_data.py --yes
Przed uruchomieniem zr?b kopi? bazy.
"""
import sys

from app.db.reset_data import count_company_data, delete_company_data


def main() -> int:
    print("Dane do usuni?cia:")
    for table, count in count_company_data().items():
        print(f"  {table}: {count}")
    if "--yes" not in sys.argv:
        print("\nTo tylko podgl?d. Aby usun??: python reset_company_data.py --yes")
        return 0
    if input("Wpisz USUN, aby potwierdzi?: ").strip() != "USUN":
        print("Anulowano.")
        return 1
    delete_company_data()
    print("Gotowe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
