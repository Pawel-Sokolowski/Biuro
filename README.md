# Biuro – program dla biura rachunkowego

Aplikacja desktopowa (PyQt6 + PostgreSQL) do prowadzenia biura rachunkowego: klienci, faktury, e-mail, kalendarz, zadania, raporty i kontrola kopii zapasowych.

> Zrzuty ekranu pochodzą z tymczasowej bazy z danymi fikcyjnymi (`scripts\make_screenshots.py`).

## Moduły

| Moduł | Opis |
|---|---|
| **Pulpit** | Podsumowanie dnia, czas pracy, stan kopii zapasowych |
| **Widok miesięczny** | Status firm w wybranym miesiącu (wybór miesiąca i roku) |
| **Klienci** | Lista firm, formularz firmy, pełnomocnictwa (PEL/UPL), data ostatniej kopii bazy |
| **E-mail** | Wiele kont IMAP/SMTP, foldery, reguły, etykiety, przypisywanie wiadomości do firm |
| **Czat** | Wewnętrzna komunikacja pracowników |
| **Faktury** | Wystawianie z cennika, usługi dodatkowe, KSeF, eksport XLSX do Saldeo |
| **Raporty** | Import dokumentów i czasu pracy, rentowność, czas według klienta i pracownika |
| **Zadania** | Zadania z czasomierzem |
| **Kalendarz** | Wydarzenia, import ICS, synchronizacja CalDAV |
| **Ustawienia** | Panel administratora (poniżej) |

## Zrzuty ekranu

![Pulpit](docs/screenshots/pulpit.png)
![Widok miesięczny](docs/screenshots/widok-miesieczny.png)
![Klienci](docs/screenshots/klienci.png)
![Faktury](docs/screenshots/faktury.png)
![Raporty](docs/screenshots/raporty.png)
![Zadania](docs/screenshots/zadania.png)
![Kalendarz](docs/screenshots/kalendarz.png)

### Panel administratora

| | |
|---|---|
| ![Użytkownicy](docs/screenshots/admin-uzytkownicy.png) | ![Role i uprawnienia](docs/screenshots/admin-role-i-uprawnienia.png) |
| ![Konta e-mail](docs/screenshots/admin-konta-e-mail.png) | ![Konto systemowe i kalendarz](docs/screenshots/admin-konto-systemowe-i-kalendarz.png) |
| ![Cennik księgowość](docs/screenshots/admin-cennik-ksiegowosc.png) | ![Cennik kadry](docs/screenshots/admin-cennik-kadry.png) |
| ![Usługi dodatkowe](docs/screenshots/admin-uslugi-dodatkowe.png) | ![Firmy](docs/screenshots/admin-firmy.png) |
| ![Faktury i KSeF](docs/screenshots/admin-faktury-i-ksef.png) | ![KSeF – dostęp i certyfikat](docs/screenshots/admin-ksef--dostep-i-certyfikat.png) |
| ![Import danych](docs/screenshots/admin-import-danych.png) | ![Baza danych i kopie](docs/screenshots/admin-baza-danych-i-kopie.png) |
| ![System](docs/screenshots/admin-system.png) | ![Audyt i importy](docs/screenshots/admin-audyt-i-importy.png) |

## Kopie zapasowe

Program czyta logi kopii (Cobian Backup, mail „Backup 11 (ENOVA)”) z pliku albo z najnowszego maila na koncie systemowym. Nazwy plików mają postać `firma_datautworzenia` (np. `Firma_20261021.BAK`), z której pobierana jest data.

**Ustawienia → Baza danych i kopie → „Sprawdź z ostatniego maila”** pobiera najnowszy mail, zapisuje datę ostatniej kopii w firmach i pokazuje tabelę *Nazwa firmy | Data | Błąd (TAK/NIE)*. Data jest widoczna w kolumnie „Ostatni backup” na liście klientów.

## Wymagania

- Python 3.11
- PostgreSQL
- Zależności: `pip install -r requirements.txt`

## Konfiguracja

Skopiuj `.env.example` jako `.env` (katalog projektu albo `%LOCALAPPDATA%\Biuro\` dla wersji zainstalowanej). Plik `.env` nie trafia do instalatora ani do repozytorium.

| Zmienna | Opis |
|---|---|
| `DB_URL` | Adres bazy PostgreSQL (wymagane) |
| `BIO_ENCRYPTION_KEY` | Klucz szyfrowania haseł kont e-mail; przy bazie na NAS ustaw ten sam na wszystkich komputerach |
| `CEIDG_BASE_URL`, `CEIDG_API_KEY` | Wyszukiwanie firm w CEIDG |
| `GUS_API_URL`, `GUS_API_KEY` | Wyszukiwarka REGON (domyślny adres jest wbudowany) |

## Uruchomienie

```powershell
pip install -r requirements.txt
python migrate_db.py   # tworzy/aktualizuje schemat bazy
python main.py
```

`migrate_db.py` kończy się sukcesem tylko wtedy, gdy schemat jest aktualny (`SCHEMA_VERSION` w `app\db\schema.py`). Zmiana struktury bazy wymaga podbicia tej wersji.

## Testy

```powershell
python run_tests.py                      # wszystkie pliki testowe
python -m unittest tests.test_invoices   # pojedynczy plik
```

Każdy test działa na osobnym, tymczasowym schemacie PostgreSQL.

## Zrzuty ekranu do dokumentacji

```powershell
python scripts\make_screenshots.py
```

Skrypt tworzy tymczasowy schemat z fikcyjnymi firmami, zapisuje obrazy w `docs\screenshots` i usuwa schemat. Dane z prawdziwej bazy nie są odczytywane; zakładka „Logi” jest pomijana.

## Instalator

Skrypt Inno Setup: `installer\biuro.iss` (wersja 1.3.1). Specyfikacja PyInstallera: `packaging\biuro.spec` (opis w `packaging\README.md`).

## Struktura

```
app\            kod aplikacji (admin, companies, dashboard, db, email, invoices, kalendarz, monthly_view, reports, tasks, utils ...)
tests\          testy unittest
scripts\        narzędzia pomocnicze
installer\      instalator Inno Setup
docs\screenshots\  zrzuty ekranu do README
```
