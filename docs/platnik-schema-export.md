# Eksport standardowej struktury bazy Płatnika

Skopiuj obok siebie `export_platnik_schema.cmd` i `export_platnik_schema.ps1`.
Uruchom CMD, np. na serwerze Płatnika. Nie wymaga Pythona ani sterownika ODBC.
Używa System.Data.SqlClient z Windows/.NET i Windows PowerShell 5.1.

1. Podaj serwer SQL/instancję oraz nazwę bazy z konfiguracji programu Płatnik.
2. Wybierz konto Windows albo podaj użytkownika i hasło **SQL Server**. Nie
   podawaj konta Biuro/PostgreSQL ani hasła użytkownika programu Płatnik.
3. Gdy SQL używa własnego certyfikatu, można jawnie wybrać zaufanie temu
   certyfikatowi. Połączenie nadal jest szyfrowane.
4. Skrypt zapisze `platnik-schema-RRRRMMDD-GGMMSS.json` na pulpicie (lub w katalogu
   roboczym, jeśli profil nie ma pulpitu). W oknie zobaczysz dokładną ścieżkę.

Eksportuje wyłącznie katalog systemowy: nazwy tabel/widoków i kolumn, typy,
indeksy/klucze i relacje. Nie wykonuje SELECT z tabel biznesowych Płatnika,
nie odczytuje dokumentów, pracowników ani haseł, nie wykonuje zmian w bazie.
JSON zawiera też nazwę wybranej bazy i wersję SQL Server; te metadane mogą
zawierać nazwy organizacji, dlatego przejrzyj plik przed udostępnieniem.
Nie zawiera adresu serwera, loginu ani hasła połączenia. Nie eksportuje kodu
procedur/widoków ani domyślnych wartości kolumn.

Konto potrzebuje widoczności metadanych (VIEW DEFINITION). Eksport obejmuje tylko
obiekty widoczne dla konta; przy ograniczonych uprawnieniach może być niepełny.
Launcher ustawia politykę wykonywania wyłącznie dla swojego procesu PowerShell;
nie zmienia ustawień Windows. Polityka domenowa może nadal zabronić uruchomienia.
Nie wymaga administratora Windows, jeśli konto ma odpowiedni dostęp do bazy.

To eksport standardowego schematu konkretnej wersji Płatnika, nie sugestia, że
baza jest niestandardowa. Nazw tabel i statusów nie zgadujemy; migracje Płatnika
mogą zmieniać strukturę. Eksport pozwoli przygotować właściwe zapytanie odczytujące
wakacje składkowe. Nie dowodzi, że wszystkie żądane statusy są lokalnie zapisane.
