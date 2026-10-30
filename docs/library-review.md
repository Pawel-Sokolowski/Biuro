# Przegląd wykorzystania bibliotek

Przegląd dotyczy konkretnych funkcji w aplikacji. Poniższe propozycje są oceną
zakresu kolejnych zmian; sam dokument nie zmienia obliczeń ani formatów danych.

| Priorytet | Miejsce | Propozycja | Warunki bezpiecznej zmiany |
|---|---|---|---|
| Wysoki | `app/invoices/positions.py`: `normalize_position`, `totals`; `app/invoices/faktury_logic.py`: obliczenia pozycji i sum; `app/services/calculator.py` | Ujednolicić kwoty na `decimal.Decimal` i jawne `quantize(Decimal("0.01"), rounding=...)` zamiast `float` i `round`. `decimal` jest w bibliotece standardowej. | Najpierw ustalić regułę zaokrąglania VAT oraz kolejność sumowania pozycji. Konwersja powinna zaczynać się od tekstu/liczb dziesiętnych, nie `Decimal(float)`. Sprawdzić połówki grosza, ilości ułamkowe, korekty, sumy faktur i zgodność z eksportem. Nie zmieniać historycznych faktur przez ponowne przeliczenie. |
| Wysoki | `app/invoices/ksef_xades.py`: `sign_auth_request` | Zastąpić ręczne składanie kanonicznego XML drzewem XML i kanonizacją `lxml.etree.tostring(..., method="c14n", exclusive=True)`. | `lxml` jest instalowane w CI do niezależnej weryfikacji podpisu; użycie w aplikacji wymagałoby dopisania zależności wykonawczej. Zachować Exclusive C14N, przestrzenie nazw, referencje XAdES i format podpisów RSA/EC. Rozszerzyć niezależną weryfikację w `tests/test_ksef_xades.py` o znaki specjalne i podpis EC. Samo `ElementTree.tostring` nie zastępuje kanonizacji podpisu. |
| Niski, mała zmiana | `app/utils/months.py`: `_last_day_of_month`; `app/utils/ceidg_suspension.py`: `_last_day` | Zastąpić obie implementacje przez `calendar.monthrange(year, month)[1]`. `app/utils/payment_terms.py` już korzysta z tej funkcji. | Zachować zachowanie dla lutego przestępnego, grudnia i końca miesiąca. Nie wymaga nowej zależności. |
| Niski | `app/utils/months.py`: `previous_month`, `month_bounds`; `app/invoices/ksef_sync.py`: obliczanie zakresu miesiąca | Korzystać ze wspólnego `month_bounds`; przy dalszych operacjach na miesiącach rozważyć `dateutil.relativedelta(months=1)`. `python-dateutil` już jest zależnością. | Zachować zakres z wyłącznym końcem miesiąca oraz świadome konwersje do UTC dla KSeF. Testować grudzień/styczeń i luty. Proste przesunięcie o stałą liczbę dni nie jest ogólną operacją „miesiąc”. |

## Biblioteki już użyte we właściwych miejscach

- Import ING MT940 w dodawanej obsłudze płatności opiera się na `mt-940`.
  Parser formatu należy pozostawić bibliotece; dopasowanie kontrahentów,
  potwierdzanie rozliczeń i wykrywanie duplikatów pozostają logiką programu.
- `app/email/email_helpers.py`: `parse_message`, `_body_content`,
  `iter_message_attachments` korzystają z `email.policy.default`, `get_body`
  i `iter_attachments`. Nie ma potrzeby zastępować ich nową biblioteką MIME.
- `app/kalendarz/ics_import.py`: `_expand` wykorzystuje `icalendar` i
  `recurring_ical_events`. Nie należy implementować własnego parsera RRULE.
- `app/utils/saldeo_contractors_import.py`: `_csv_rows`, `_xlsx_rows` używają
  `csv.Sniffer`/`csv.reader` oraz `openpyxl.load_workbook(read_only=True)`.
  Obsługa nagłówków Saldeo i kodowania CP1250 pozostaje potrzebną warstwą aplikacji.
- `app/utils/parsing.py`: `parse_date_value` już korzysta z `dateutil.parser`.
  Zachować ograniczenie do rozpoznawanych formatów i polskie `dayfirst`; liberalny
  parser bez tych zasad może błędnie odczytać datę `07/09/2026`.

## Funkcje, które warto zachować

`app/utils/polish_sort.py`: `polish_sort_key` zapewnia określony porządek niezależny
od systemu. `locale.strxfrm` wymaga dostępnej polskiej lokalizacji i ustawień
globalnych procesu, więc nie jest równoważnym zamiennikiem.

`app/companies/address_parsing.py`: `split_street_address` zachowuje osobne pola
z rejestrów i nie zgaduje przy sprzecznych danych. Ogólny parser adresów mógłby
zmienić tę regułę. Podobnie reguły rozpoznawania statusów Saldeo i nazw kopii
zapasowych są zależne od domeny; sama zamiana wyrażeń regularnych na bibliotekę
nie upraszcza odpowiedzialności programu.

Kodowanie folderów IMAP w `app/email/email_helpers.py` to **zmodyfikowany UTF-7**.
Zwykły kodek `utf-7` nie jest poprawnym zamiennikiem. Ewentualne przeniesienie do
innego klienta IMAP wymaga osobnej oceny i zachowania istniejących testów folderów.
