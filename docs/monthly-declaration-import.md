# Deklaracje za cały rok i miesięczne kadry

W **Sprawdzaniu** wybierz **Pobierz deklaracje za cały rok — Enova i Płatnik**,
rok oraz firmy. Zlecenie wykonuje worker na serwerze. Pobiera formularze ZUS
za styczeń–grudzień, IWA/ZSWA za wskazany rok oraz deklaracje podatkowe i JPK
z powiązanych baz Enovy. Poszczególne źródła mają osobne wyniki; błąd jednego
nie oznacza, że drugie zostało odczytane.

Import dodaje wykryte deklaracje do **Ustawienia miesięcznego** firmy. Nie
wyłącza pozostałych obowiązków na podstawie braku dokumentu i nie nadpisuje
ręcznie wskazanych ustawień. Pokazuje źródło wykrycia w ustawieniu miesięcznym.
Rozpoznany wariant VAT-7K/JPK V7K podpowiada rozliczenie kwartalne, z miesięczną
ewidencją JPK; ręcznie wybrany okres ma pierwszeństwo. Jest to uzupełnienie
zakresu deklaracji, bez importowania kwot, liczników dokumentów czy zmiany etapów.
Zapisane informacje nie są dowodem wysłania ani przyjęcia deklaracji.

W **Widoku miesięcznym → Kadry** ostatnia kolumna zawiera kody ubezpieczenia
z dokumentów Płatnika za wybrany miesiąc. Dymek podaje osobę, formularz, NIP
płatnika, dokument źródłowy i datę odczytu. Brak odczytu nie jest kodem zero;
kodów nie przenosi się automatycznie na kolejne miesiące.

Płatnik odczytuje kod z pól `ZUSDRA.XI_1_*` oraz `UBEZP_ZUSRCA`,
`UBEZP_ZUSRSA` i `UBEZP_ZUSRZA` (`III_B_1_*`), gdy metadane potwierdzają ich
obecność. Cztery cyfry tytułu nie są uzupełniane domyślnymi zerami przy braku
danych o emeryturze/rencie lub niepełnosprawności. W spółce cywilnej deklaracje
wspólników pod indywidualnymi NIP-ami trafiają do tej samej firmy, z zachowaniem
NIP-u źródłowego; nie nadpisują deklaracji spółki ani drugiego wspólnika.

Mapowanie pól opisuje publiczna dokumentacja ZUS:
[Struktury danych osobowych Płatnika 1.9](https://ec06-waw1.cache.orange.pl/zus/pobierz/dystrybucja/a1_10_02_002/dokumentacja/Struktury_danych_osobowych_wersja_1.9.pdf),
rozdział 4.3. Prywatne eksporty schematów nie są publikowane.

# Klienci i poczta

Przy odczycie listy folderów program wiąże istniejący folder z klientem, jeśli
pasuje jednoznacznie jego nazwa, nazwa skrócona, NIP albo dane wspólnika.
Uwzględnia kodowanie IMAP i polskie znaki. Tworzy reguły dla głównego adresu,
kontaktów i wspólników. Nie tworzy nowych folderów, nie zastępuje ręcznych
reguł i pomija niejednoznaczne nazwy, wiele pasujących folderów lub adres
wspólny dla różnych firm. Powiązanie powstaje również przy zapisie kontaktów
w kartotece klienta. Reguły służą poczcie przychodzącej i wyborowi kopii
wychodzących; sam zapis powiązania nie przenosi starszych wiadomości.

Mail masowy wysyła odbiorców w kopercie SMTP BCC. Nagłówek BCC nie trafia do
odbiorców. Program zapisuje kopię partii w serwerowym folderze **Wysłane**
oraz osobne kopie w powiązanych folderach klientów; każda kopia klienta zawiera
wyłącznie jego adresy BCC. Adres odrzucony przez SMTP nie jest oznaczany w kopii
jako przyjęty do wysyłki. Po błędzie IMAP raport wskazuje, że wysyłka SMTP się
powiodła, lecz kopia nie została potwierdzona. Nie wolno wtedy ponawiać mailingu
tylko w celu odtworzenia kopii. Odpowiedź SMTP nie dowodzi doręczenia do skrzynki
odbiorczej; produkcyjne doręczenie potwierdzają logi serwera poczty i zwrotki.
