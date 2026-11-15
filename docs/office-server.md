# Połączenie stanowisk z usługą Biuro

Stanowisko pracownika podaje adres HTTPS serwera, e-mail i hasło pracownika.
Nie wymaga PostgreSQL, DB_URL ani BIO_ENCRYPTION_KEY. Nie istnieje funkcja pobierania
lub synchronizowania klucza. Serwer wykonuje operacje na bazie i połączenia z pocztą,
Enovą, Płatnikiem, KSeF i e-ZLA z użyciem swoich lokalnych danych dostępu.

## Uruchomienie na serwerze Windows

1. W istniejącym panelu `Biuro.exe --worker-setup` zachowaj konfigurację bazy
   i dotychczasowy klucz szyfrowania na koncie uruchamiającym worker. Dane te
   nie są przekazywane stanowiskom. Kopia zapasowa klucza należy do administratora.
2. W zakładce **Usługa HTTPS** włącz usługę, wybierz adres nasłuchu i port (domyślnie
   8443), podaj ścieżki do certyfikatu PEM i prywatnego klucza TLS PEM. Certyfikat
   musi odpowiadać nazwie serwera i być zaufany na komputerach pracowników.
   Certyfikat TLS jest odrębnym kluczem od klucza szyfrowania danych Biuro.
3. Zapisz ustawienia i uruchom ponownie worker na tym samym koncie Windows.
   Worker uruchamia HTTPS niezależnie od włączenia poszczególnych zadań importu.
4. Zezwól komputerom biura/VPN na połączenie z portem HTTPS. PostgreSQL i pliki
   konfiguracyjne udostępniaj wyłącznie serwerowi. Stanowiskom podaj np.
   `https://biuro.example.internal:8443`.
5. Uruchom zwykłe Biuro na stanowisku, wpisz adres i zaloguj się kontem pracownika.
   Opcja zapamiętania logowania zapisuje token sesji w Menedżerze poświadczeń
   Windows, bez hasła pracownika, hasła bazy lub klucza biura.

Opcjonalnie samą usługę uruchamia `Biuro.exe --api-server` lub
`python main.py --api-server`, korzystając z tej samej konfiguracji serwerowej:
`BIO_API_BIND`, `BIO_API_PORT`, `BIO_API_CERT`, `BIO_API_TLS_KEY`.
`BIO_API_ENABLED=1` włącza usługę wewnątrz workera; samodzielne `--api-server`
uruchamia ją bez zadań importu.

Pierwszego administratora, aktualizację schematu i lokalną konfigurację klucza
wykonuje się na serwerze. `--direct` jawnie uruchamia lokalny panel administracyjny
z konfiguracją serwera; aplikacja pracownika nie wraca automatycznie do tego trybu
w przypadku niedostępności HTTPS. `--migrate` / `--worker-migrate` pozostają
lokalnymi poleceniami serwera.

Pierwsze logowanie OAuth Google oraz logowanie Saldeo z 2FA wykonaj lokalnie w
panelu workera na serwerze. Późniejsze operacje wykorzystują zapisane tam sesje.
Niestandardowe pliki SQL przesłane przez administratora są przechowywane na
serwerze; dołączone do instalatora zapytania nadal działają domyślnie.

## Sesje i uprawnienia

Serwer przechowuje wyłącznie skrót tokena sesji. Zwykła sesja wygasa po 12 godzinach,
zapamiętana po 14 dniach. Wylogowanie unieważnia token; dezaktywacja pracownika
lub zmiana/reset hasła odbiera dostęp istniejącym sesjom przy następnym żądaniu.
Każda operacja ponownie sprawdza uprawnienia i przypisanie poczty/rozmowy/wydarzenia.
Identyfikator wykonującego operację jest nadawany przez serwer.

API udostępnia zamknięty katalog operacji biznesowych. Nie przyjmuje SQL, nazw
funkcji spoza katalogu, połączeń z bazą ani ścieżek do pobrania dowolnego pliku
serwera. Importy i eksporty używają plików przypisanych do konkretnej sesji.
Długie operacje przekazują postęp i obsługują przerwanie.

**Mój czas** w nagłówku pokazuje czas pracownika ze wszystkich firm i stoper
ogólny, z filtrem miesiąca. Pracownik może poprawiać własne rekordy. Wgląd i edycja
czasu innych osób wymaga uprawnień finansowych lub administratora. Edycja **Od–do**
wylicza czas i aktualizuje sumy miesięczne; w rekordach importowanych odejmuje
zapisaną przerwę. Importowany rekord obejmuje jeden dzień. Pozostaje też podanie
samego czasu trwania. Zmiany i usunięcia są zapisywane w audycie.
