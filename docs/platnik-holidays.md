# Płatnik — roczne wakacje składkowe (1.5.3)

## Konfiguracja

W Biuro Worker otwórz **Płatnik** (lub w aplikacji Ustawienia → Płatnik — połączenie).
Dla podanej przez użytkownika instalacji serwer/instancja to
`platnik-srv\SQLEXPRESS`, baza `platnik`. Są to oddzielne pola, nie „podbaza”.
Wprowadź konto SQL z prawem SELECT, hasło oraz sterownik (automatycznie, 17 lub 18).
Jeśli serwer używa własnego certyfikatu, zaznacz odpowiednią opcję. Połączenie
pozostaje szyfrowane. Hasło jest przechowywane zaszyfrowane wspólnym kluczem Biuro.

Sprawdź połączenie i zapisz ustawienia, włącz odczyt Płatnika. Możesz pobrać
wskazany rok ręcznie w tym panelu. W Zadaniach workera włącz osobne zadanie
Płatnik; domyślnie co godzinę odświeża poprzedni i bieżący rok. Starszy rok
pobierz ręcznie. Nie ma zapisu do Płatnika ani wysyłania dokumentów do ZUS.
Nie jest wymagany własny plik SQL ani instalacja Płatnika na workerze.

## Widok

**Kadry → Sprawdzanie → Wakacje składkowe — Płatnik (rocznie)** ma własny wybór roku.
Miesiąc jest kolumną wpisu ulgi, nie filtrem widoku. W tym raporcie miesięczny
selektor i filtry dokumentów są ukryte. Tabela pokazuje firmę oraz zamówione kolumny:
imiona i nazwiska, kod ubezpieczenia, wysłane powiadomienie, składki opłacane,
miesiąc ulgi, oświadczenie, płatność, kto wysłał, decyzja/status wpisu.

Brak wpisu RWS w odczycie nie oznacza odmowy ani braku uprawnienia. Takie firmy
pozostają widoczne. Bez poprawnego importu wyświetlamy „Nie odczytano roku”.
Najnowsza nieudana próba nie usuwa poprzedniego snapshotu; widok zaznacza nieaktualność.
Przełączenie roku czyści poprzednie wiersze podczas pobierania zapisu, aby nie
pokazywać starego roku pod nowym nagłówkiem. Importy jednego roku mają blokadę
PostgreSQL zapobiegającą równoczesnej pracy kilku wywołań.

## Zakres potwierdzony eksportem

Użytkownik przekazał schemat SQL Server 14.0.1000.169 (SQL 2017). Adapter czyta
`dbo.RWS_ZWOLNIENIE`, powiązania `ID_PLATNIK` z `PLATN_IDENT` oraz dane osoby
z `UBEZP_IDENT` po lokalnym ID lub identyfikatorach ZUS. Historyczne, różniące się
NIP-y lub nazwy nie są rozstrzygane wyborem przypadkowego rekordu; wiersz wymaga
sprawdzenia. Osoby bez jednoznacznych danych pozostają w raporcie z ostrzeżeniem.
Nie odczytujemy PESEL-i ani haseł użytkowników Płatnika.

Znaczenie A/N przyjęto zgodnie z informacją użytkownika:
- A: aktualne zwolnienie;
- N: nieaktualny wpis, nie decyzja odmowna.

Zachowujemy kod oraz wszystkie wpisy, w tym N. Opcjonalny filtr pokazuje tylko A.
Nieznane kody nie są mapowane na zgodę/odmowę. Sam wpis RWS nie jest dowodem
pobrania formalnej decyzji/UPO. Kwoty składek RWS są w szczegółach komórki,
wyraźnie jako kwoty źródłowe, nie potwierdzenie zapłaty.

**Obecny adapter nie odczytuje jeszcze powiadomień do klientów, oświadczeń,
faktycznych płatności, nadawcy ani formalnej decyzji/UPO z innych tabel.**
Odpowiednie kolumny pokazują „Brak danych”, nigdy automatyczne Tak/Nie. Mimo że
baza ma tabele ZESTAW/ZESTAW_DOKUM i potwierdzenia, ich powiązanie ze zdarzeniami
RWS wymaga osobnego adaptera i weryfikacji znaczenia dokumentów. Nie wywodzimy
nadawcy RWS z użytkownika, który jedynie utworzył kartotekę płatnika.

Kompletne, zweryfikowane dane roczne są atomowo zapisywane jako zaszyfrowany
snapshot w PostgreSQL Biuro. Pracownicy potrzebują dostępu do Sprawdzania oraz
firmy według istniejących reguł. Nieprzypisane NIP-y widzi wyłącznie administrator.
Instalacja nie wymaga zmiany schematu PostgreSQL — używa istniejącego app_settings.

Weryfikację testów i budowy EXE wykonuje GitHub CI. Po wdrożeniu sprawdź test
połączenia, wykonaj odczyt 2026 i porównaj liczbę wpisów źródłowych z podanym
wynikiem (9 × A), bez wyciągania wniosków z liczby dopasowanych firm lub nazwisk.
