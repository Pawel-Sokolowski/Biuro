# Przegląd wykorzystania bibliotek

Stan po przeglądzie z 4 listopada 2026 r. Pełna lista wdrożonych zmian i decyzji:
[optimization-fixes.md](optimization-fixes.md).

| Obszar | Aktualne rozwiązanie |
|---|---|
| Połączenia PostgreSQL | `psycopg_pool` zamiast własnej puli; ograniczenie aktywnych połączeń, sprawdzanie stanu i jawne zwracanie transakcji. |
| Migracje | Alembic z zamrożonym bootstrapem istniejących baz; kolejne uruchomienie migracji nie wykonuje całej historii zmian. |
| XAdES | Drzewo i Exclusive C14N przez istniejące `lxml`; osobna weryfikacja RSA/EC oraz skrótów referencji. |
| Foldery IMAP | Kodowanie zmodyfikowanego UTF-7 przez `IMAPClient`. Generacja UID i zasady synchronizacji pozostają odpowiedzialnością programu. |
| Kwoty | `decimal.Decimal` i jawne HALF_UP w obliczeniach pozycji, cen i kwot XML. Zachowano istniejące interfejsy JSON/UI i historyczne dokumenty. |
| Koniec miesiąca | `calendar.monthrange` z biblioteki standardowej. |
| Zadania w tle | Qt `QThreadPool`/`QRunnable`, ograniczona liczba pracowników i odrzucanie spóźnionych wyników odczytów. |
| Arkusze | `openpyxl` w trybie read-only, zamykanie plików oraz porcjowanie zapisów przez psycopg. |

Biblioteki prawidłowo używane wcześniej: `mt-940`, standardowe `email` i `csv`,
`icalendar`, `recurring_ical_events`, `caldav`, `cryptography`, `bcrypt` oraz `qrcode`.
Kalendarzowe zależności importowane bezpośrednio są teraz zadeklarowane jawnie.
Usunięto nieużywane `django-qrcode`; zależności wydania są utrwalone w
`requirements.lock` razem ze skrótami pakietów.

Zachowano reguły domenowe: sortowanie polskich nazw, adresy i pola rejestrów,
statusy Saldeo, dopasowanie wpłat, deklaracje roczne oraz zasady VAT. Nie zastępuje
ich ogólny parser ani biblioteka automatycznych ponowień. Zeep, python-stdnum,
email-validator, Pydantic i pełna zmiana klienta IMAP wymagają osobnej oceny
kompatybilności lub zasad walidacji; nie są potrzebne do opisanych napraw.

## Wersje sprawdzone 21 listopada 2026 r.

Wersje Biuro pochodzą z plików `*.lock`. Kolumna upstream wskazuje
wydania stabilne potwierdzone w PyPI. CI testuje aplikację i instalator
na Pythonie 3.14. [Workflow testów](../.github/workflows/tests.yml)

| Pakiet | Biuro | Upstream | Zakres testu przed aktualizacją |
|---|---|---|---|
| [pyodbc](https://pypi.org/project/pyodbc/) | 5.5.0 | 5.5.0 | Sterownik ODBC i odczyt SQL Server. |
| [psycopg](https://pypi.org/project/psycopg/), [psycopg-binary](https://pypi.org/project/psycopg-binary/) | 3.3.8 | 3.3.9 | Razem w lockfile aplikacji i workera. Test migracji oraz transakcji. |
| [psycopg-pool](https://pypi.org/project/psycopg-pool/) | 3.3.1 | 3.3.3 | Test zwracania połączeń po błędzie i zamykania puli. |
| [Alembic](https://pypi.org/project/alembic/) | 1.20.1 | 1.20.1 | Migracje pustej i istniejącej bazy. |
| [PyQt6](https://pypi.org/project/PyQt6/), [Charts](https://pypi.org/project/PyQt6-Charts/), [WebEngine](https://pypi.org/project/PyQt6-WebEngine/) | 6.11.0 | 6.12.1, 6.12.1, 6.140.1 | Sprawdzić wymagane biblioteki Qt i SIP oraz uruchomić instalator Windows z WebEngine. |
| [pypdf](https://pypi.org/project/pypdf/) | 6.21.4 | 7.1.1 | Zakres `<7` wymaga świadomej zmiany. Test generowania, łączenia i dołączania faktur PDF. |
| [Playwright](https://pypi.org/project/playwright/) | 1.56.0 | 1.65.2 | Test logowania i odczytu Saldeo w instalacji z używaną wersją Edge lub Chrome. |
| [CalDAV](https://pypi.org/project/caldav/), [icalendar](https://pypi.org/project/icalendar/) | 2.1.2, 7.3.0 | 3.7.0, 7.3.2 | Test powtarzalności, stref czasowych i synchronizacji zmian w obie strony. |
| [IMAPClient](https://pypi.org/project/IMAPClient/) | 3.0.1 | 4.3.0 | Test UID, folderów i ponownego łączenia. |
| [python-dateutil](https://pypi.org/project/python-dateutil/) | 2.9.0.post0 | 4.0.0 | Sprawdzić zgodność z bibliotekami kalendarza i zachowanie zdarzeń cyklicznych. |
| [openpyxl](https://pypi.org/project/openpyxl/), [XlsxWriter](https://pypi.org/project/XlsxWriter/) | 3.1.5, 3.3.1 | 3.1.5, 3.3.3 | Zachować kierunek odczytu i zapisu. Test arkusza z formułami i kwotami. |
| [mt-940](https://pypi.org/project/mt-940/) | 5.1.3 | 5.1.3 | Parser MT940 oraz test bankowych wariantów pola :86:. |
| [lxml](https://pypi.org/project/lxml/), [cryptography](https://pypi.org/project/cryptography/), [bcrypt](https://pypi.org/project/bcrypt/) | 6.1.3, 50.0.2, 5.0.0 | 6.1.3, 50.0.2, 5.0.0 | Test podpisów XML, szyfrowania i logowania. |
| [pyzipper](https://pypi.org/project/pyzipper/) | 0.4.0 | 0.4.0 | Zachować obsługę szyfrowanych archiwów AES z ZUS. |
| [google-auth](https://pypi.org/project/google-auth/), [google-auth-oauthlib](https://pypi.org/project/google-auth-oauthlib/) | 2.62.1, 1.5.0 | 2.63.0, 1.5.0 | Test tworzenia i odświeżania tokenów. |
| [PyInstaller](https://pypi.org/project/PyInstaller/) | 6.16.0 | 6.22.3 | Test zbudowanego instalatora, wtyczek Qt i adapterów ODBC. |
| [Ruff](https://pypi.org/project/ruff/), [mypy](https://pypi.org/project/mypy/), [Hypothesis](https://pypi.org/project/hypothesis/) | 0.15.6, 1.19.1, 6.170.14 | 0.17.6, 2.4.1, 6.170.17 | Uruchomić testy i analizę statyczną osobno od zmian bibliotek produkcyjnych. |
| [PyWinRT](https://pypi.org/project/winrt-runtime/) | 4.1.0 | 4.2.0 | Sprawdzić zgodne koła modułów Windows i test powiadomień. |

W sprawdzonych wydaniach bez różnic były także
[Requests 2.34.2](https://pypi.org/project/requests/),
[python-dotenv 1.2.4](https://pypi.org/project/python-dotenv/),
[python-docx 1.2.0](https://pypi.org/project/python-docx/),
[docxtpl 0.20.2](https://pypi.org/project/docxtpl/),
[python-stdnum 2.2](https://pypi.org/project/python-stdnum/),
[qrcode 8.2](https://pypi.org/project/qrcode/),
[recurring-ical-events 3.8.2](https://pypi.org/project/recurring-ical-events/)
i [tzdata 2026.6](https://pypi.org/project/tzdata/).

Pierwszym kandydatem do aktualizacji jest razem para psycopg i psycopg-binary
oraz pula. Oddzielny build Windows pozwoli zweryfikować Qt i PyInstaller.
Przegląd obejmuje pakiety bezpośrednie i wybrane pakiety binarne. Aktualizacje
wymagają ponownego rozwiązania zależności i wygenerowania lockfile ze skrótami.

### Funkcje biblioteki standardowej i reguły Biuro

- Granice miesiąca korzystają z `calendar.monthrange` i `datetime.timedelta`.
  Moduły raportów nadal wyznaczają własne okresy rozliczeniowe, np. miesiąc
  ustawowy wakacji ZUS. [Dokumentacja calendar](https://docs.python.org/pl/3.14/library/calendar.html)
- Import miesięczny i odczyt rejestru współdzielą funkcję budowania parametru
  XML przez `xml.etree.ElementTree`. Funkcja zachowuje normalizację NIP-u.
  [Dokumentacja ElementTree](https://docs.python.org/pl/3.14/library/xml.etree.elementtree.html)
- Składki DRA/RCA korzystają z `Decimal` i istniejącej funkcji
  `money()`. Zasady wyboru korekt, finansowania i rozdziału groszy
  pozostają regułami domenowymi. [Dokumentacja decimal](https://docs.python.org/pl/3.14/library/decimal.html)
- `python-stdnum` warto wykorzystać do walidacji NIP i polskich rachunków
  w miejscach wprowadzania danych. Dopasowywanie rekordów historycznych
  zachowuje obecną normalizację, ponieważ wymuszenie sumy kontrolnej może
  wykluczyć zaakceptowane wcześniej rekordy. [Dokumentacja python-stdnum](https://arthurdejong.org/python-stdnum/)
- Wstęp do C API dotyczy rozszerzeń i osadzania interpretera. Do raportu
  miesięcznego wystarczają moduły Pythona i dotychczasowe zależności.
  [Wprowadzenie do C API](https://docs.python.org/pl/3.14/c-api/intro.html)
