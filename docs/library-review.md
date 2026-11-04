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
