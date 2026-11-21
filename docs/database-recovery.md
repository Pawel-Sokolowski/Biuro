# Kopia zapasowa i odtworzenie Biuro

Na VM PostgreSQL wykonuj kopie bazy Biuro zgodnie z ustaloną retencją. Kopia
plików Enovy lub wpis „Ostatni backup” w kartotece dotyczy innego źródła.
Przed aktualizacją schematu przygotuj pełną kopię PostgreSQL i potwierdź, że
da się ją odtworzyć. W katalogu kopii zapisz czas wykonania, wersję PostgreSQL,
wersję Biuro oraz identyfikator wydania.

## Przygotowanie kopii

1. Wykonaj kopię narzędziem `pg_dump` w formacie custom, używając konta z
   uprawnieniami do odczytu całej bazy. Wybierz narzędzia z wersji serwera
   PostgreSQL albo nowszej zgodnej wersji. Przykład dla PowerShell:

   ```powershell
   $plik = Join-Path 'D:\Kopie\Biuro' ("biuro-{0}.dump" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
   pg_dump --host=biuro-db.firma.local --username=biuro_backup --dbname=biuro --format=custom --file="$plik"
   if ($LASTEXITCODE) { throw 'pg_dump zakończył się błędem' }
   pg_restore --list "$plik"
   if ($LASTEXITCODE) { throw 'Nie da się odczytać archiwum PostgreSQL' }
   Get-FileHash -Algorithm SHA256 "$plik"
   ```

2. Zabezpiecz oddzielnie wspólny klucz szyfrowania Biuro, konfigurację workera
   oraz dane potrzebne do odtworzenia jego certyfikatu. Klucz Biuro z czasu
   wykonania kopii pozwala otworzyć zaszyfrowane ustawienia i hasła. Zachowaj
   również wymagane role PostgreSQL i ich uprawnienia, które odtwarza się z
   dokumentacji wdrożenia lub chronionej kopii ról.
3. Zabezpiecz pliki wymagane przez używane integracje, w tym prywatne klucze i
   lokalne adaptery SQL. Ogranicz dostęp do kopii oraz kluczy według ról.
   Test odtworzenia korzysta z kopii roboczych, a oryginał archiwum pozostaje
   do użycia w razie awarii.

## Próba odzyskania na zapasowej VM

1. Przygotuj osobny serwer lub bazę o nazwie testowej i odizoluj w systemie
   testowym połączenia do SMTP, KSeF, Enovy, ZUS i pozostałych produkcyjnych
   usług. Pozostaw zadania automatyczne workera wyłączone przewidzianą do
   tego konfiguracją systemową, np. przez wyłączone zadanie Harmonogramu.
2. Odtwórz kopię do **nowej, pustej bazy**, po zweryfikowaniu nazwy i hosta.
   Przykład:

   ```powershell
   createdb --host=biuro-test.firma.local --username=postgres biuro_odtworzenie
   if ($LASTEXITCODE) { throw 'Nie można przygotować pustej bazy' }
   pg_restore --host=biuro-test.firma.local --username=postgres --dbname=biuro_odtworzenie --exit-on-error --single-transaction --no-owner --no-privileges "$plik"
   if ($LASTEXITCODE) { throw 'Odtwarzanie bazy zakończyło się błędem' }
   ```

3. Nadaj rolom uprawnienia w środowisku odtworzeniowym. Skonfiguruj na
   odtwarzanej VM adres tej bazy, dotychczasowy klucz Biuro i osobny
   certyfikat HTTPS. W razie potrzeby uruchom migrację z tym środowiskiem
   wskazanym przez `DB_URL`. Sprawdź zgodność wersji w konfiguratorze.
4. Uruchom samą usługę `Biuro.exe --api-server` i zaloguj się testowym
   klientem. Sprawdź liczby firm, deklaracji, faktur i rozliczeń oraz odczyt
   zaszyfrowanego ustawienia przez **Sprawdź połączenie** w konfiguratorze
   workera. Potwierdź przykładowe dane z daty kopii i zanotuj czas odzyskania.
5. Przy faktycznym odzyskiwaniu włącz zadania automatyczne po potwierdzeniu
   poprawności danych, połączeń do właściwych systemów i stanu kolejki wysyłek.

[Konfiguracja workera](worker-configuration.md) · [Serwer HTTPS](office-server.md)
