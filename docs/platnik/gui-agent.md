# Agent Płatnika w interaktywnej sesji Windows

Agent odczytuje status już istniejących zestawów DRA i RCA oraz identyfikator
elektronicznego potwierdzenia widoczny w Płatniku. Po włączeniu pobierania
agent może też pobrać i zarejestrować potwierdzenie z ZUS. W Biuro wyniki są
w osobnej kolumnie **Agent Płatnika**. Dotyczą wysyłki zestawu. Status poszczególnego
dokumentu, odczytany z dotychczasowego źródła, ma własną kolumnę.

```text
Składki ZUS (miesięcznie) → kolejka PostgreSQL → agent Windows → Płatnik
                           ↑____________________________________|
```

Agent może działać obok PostgreSQL, jeśli VM ma Windows i dostęp do SQL Servera
Płatnika. Dotychczasowy worker Biuro zachowuje swój harmonogram. Konfiguracja
agenta używa osobnego zadania `Biuro-Platnik-GUI-Agent` z trybem logowania
interaktywnego i wyzwalaczem przy zalogowaniu użytkownika. Gdy pulpit wygaśnie
lub zostanie zablokowany, zadania czekają na sesję. Windows i sposób zdalnego
dostępu mogą zmieniać zachowanie pulpitu po rozłączeniu RDP.
[Microsoft Task Scheduler](https://learn.microsoft.com/windows/win32/api/taskschd/ne-taskschd-task_logon_type)
· [pywinauto Remote Execution Guide](https://pywinauto.readthedocs.io/en/latest/remote_execution.html)

## Baza i konto

1. Zaktualizuj serwer Biuro i wykonaj migrację `20261121_08` poleceniem
   `python migrate_db.py`.
2. Przygotuj osobny login PostgreSQL na potrzeby agenta. Konto właściciela
   schematu nadaje dostęp do dwóch funkcji kolejki. Login agenta powinien mieć
   możliwość połączenia z bazą oraz uprawnienia z poniższego skryptu.
3. Na koncie Windows agenta skonfiguruj usługę libpq
   `biuro_platnik_agent` w `%APPDATA%\postgresql\.pg_service.conf`.
   Ustaw `host`, `port`, `dbname` i `user` zgodnie z konfiguracją Biuro.
   Hasło przechowuj w pliku haseł libpq dostępnym dla tego konta Windows.
   Przy połączeniu sieciowym użyj TLS z weryfikacją nazwy serwera i jego
   certyfikatu.
   [Konfiguracja usług libpq](https://www.postgresql.org/docs/current/libpq-pgservice.html)
   · [Plik haseł libpq](https://www.postgresql.org/docs/current/libpq-pgpass.html)

```sh
psql -d biuro -v schema=public -v agent_role=biuro_platnik_agent -f scripts/grant_platnik_gui_agent.sql
```

Funkcja przydziału blokuje pobieranie drugiego zadania do czasu zakończenia
pierwszego lub wygaśnięcia dzierżawy. Zapisy wyniku wymagają aktualnego tokenu.
Po przerwaniu procesu zadanie wraca do kolejki, a następne przerwanie po trzeciej
próbie jest widoczne jako błąd. Wynik i historia zleceń są związane z firmą,
NIP-em płatnika i okresem.

## Profil Płatnika

1. Zainstaluj oficjalnego Płatnika na Windows i połącz go z używaną bazą SQL
   Server. Zaloguj dedykowane konto Windows i sprawdź uprawnienia do odczytu
   rejestru w Płatniku.
2. Skopiuj [przykład profilu](gui-agent.example.json) do lokalnego katalogu
   agenta. Uzupełnij ścieżkę EXE i dokładny tytuł lub klasę głównego okna.
   W instalacji z `p2.exe` i `P2start.exe` podaj `p2.exe` jako **Plik Płatnika**
   (`executable`) oraz `P2start.exe` jako **Program uruchamiający** (`launcher`).
   Jeśli tytuł zmienia się po wybraniu płatnika, użyj samej klasy okna.
   Etykiety w przykładzie służą do zilustrowania formatu.
3. W Płatniku otwórz rejestr dokumentów lub zestawów z widocznymi polami
   NIP-u, miesiąca, formularza, numeru deklaracji, identyfikatora zestawu,
   stanu i potwierdzenia. Numer deklaracji jest częścią identyfikatora
   rozliczenia, niezależną od numeru zestawu. Dla etykiety `sent` potrzebna
   jest również kolumna z datą i godziną wysyłki.
4. Polecenie `describe` pokazuje typ i identyfikator widocznych kontrolek.
   Uzupełnij identyfikator rejestru, pozycje nagłówków od zera oraz dokładne
   wartości stanów. W trybie UIA pole **Tabela rejestru** przyjmuje
   `class=NazwaKlasy`, jeśli klasa jednoznacznie wskazuje tabelę i jej
   `AutomationId` zmienia się między sesjami. **Wczytaj z Płatnika…** podaje
   wzorce UIA dostępne dla wykrytych tabel. Po uzupełnieniu pól wybierz
   **Sprawdź odczyt tabeli** przy otwartym rejestrze z danymi. Przycisk
   sprawdza nagłówki i odczyt pierwszego wiersza bez wyświetlania jego danych
   ani pobierania zadania z kolejki.

```powershell
py -3.14 -m venv "$env:LOCALAPPDATA\Biuro\PlatnikAgent\venv"
$agentPython = "$env:LOCALAPPDATA\Biuro\PlatnikAgent\venv\Scripts\python.exe"
& $agentPython -m pip install -r requirements-platnik-agent.txt
& $agentPython -m app.integrations.platnik.gui_agent describe --config C:\Biuro\platnik-agent.json
```

UIA używa `GridPattern` i `TablePattern`; opcjonalne `tabs` to kolejność
zakładek UIA wybieranych po AutomationId. Backend `win32` obsługuje klasyczną
tabelę `SysListView32` z nagłówkami. Wersje Płatnika mogą inaczej eksponować
kontrolki lub pokazywać część pól w osobnym oknie. Taki przypadek wymaga
sprawdzenia na docelowej instalacji i dostosowania odczytu. Jeśli rejestr nie
spełnia profilu, agent zwraca błąd zamiast przypisywać status.

## Pobieranie potwierdzeń z ZUS

Aby agent pobierał potwierdzenia automatycznie przy **Sprawdź wysyłkę w
Płatniku**, dodaj do lokalnego profilu sekcję `confirmation_download`.
Profil bez tej sekcji działa w trybie odczytu. Sekcja opisuje tabelę
**Zestawy wysłane**. Numer zestawu musi odpowiadać numerowi w tytule okna
**Zestaw dokumentów nr …**. Jeżeli tabela zawiera identyfikator
potwierdzenia, dodaj też kolumnę `receipt`, aby agent porównał oba pola.

```json
"confirmation_download": {
  "tabs": ["AutomationId zakładki Zestawy wysłane"],
  "grid": {"auto_id": "AutomationId tabeli Zestawy wysłane"},
  "columns": {
    "set_id": {"index": 0, "header": "Numer zestawu"}
  }
}
```

Wartości w przykładzie wymagają dopasowania do instalacji Windows.
Główne `tabs` w profilu UIA muszą wskazywać drogę powrotną do
rejestru deklaracji, gdy widok Zestawy wysłane wymaga przełączenia
zakładek. W profilu Win32 użyj `control_id` lub `class_name` dla
widocznej tabeli i pomiń `tabs`. Pobieranie korzysta z natywnego menu
Płatnika i polskiego kreatora potwierdzania.

Agent wybiera zestawy DRA/RCA dla zleconego NIP-u i miesiąca. Pobiera
potwierdzenie tylko dla zestawu o stanie `sent` i z zapisanym
identyfikatorem potwierdzenia. Uruchamia polecenie **Pobierz i rejestruj
potwierdzenie**. Płatnik weryfikuje podpis ZUS i rejestruje pobrany dokument,
a agent odczytuje zaktualizowany stan z Płatnika i przekazuje go do Biuro. Plik potwierdzenia
pozostaje zapisany przez Płatnika w jego bazie i katalogu przesyłek.
[Instrukcja Płatnika, rozdział 5.6.7](https://pobierz.zus.pl/dystrybucja/a1_10_02_002/dokumentacja/Dok_Uzytk_A1_10.02.002_wersja_7.8.pdf)

Jeśli trzeba sprawdzić zgodność instalacji z agentem, uruchom przechwytywanie
z aktywnego pulpitu Płatnika. Otwórz **Zestawy wysłane** i przejdź przez kreator
**Pobierz i rejestruj potwierdzenie** dla jednego wysłanego zestawu. Zakończ
przechwytywanie przez Ctrl+C. Plik zawiera metadane kontrolek i wzorce UIA.

```powershell
& $agentPython -m app.integrations.platnik.gui_agent capture-walkthrough
```

## Codzienne sprawdzanie wysyłki

W ustawieniach workera Biuro włącz **Płatnik GUI — codzienne sprawdzanie
wysyłki**. Domyślna godzina to **16:00 czasu polskiego**. Worker Biuro zleca
jedną serię dziennie dla płatników z bazy Biuro i dwóch okresów, bieżącego
oraz poprzedniego miesiąca. Ponowne uruchomienie workera tego samego dnia
nie dodaje drugiej serii. Jeśli worker uruchomi się po ustawionej godzinie,
zleci serię przy pierwszej okazji tego dnia.

Agent Windows odbiera zlecenia, gdy jego pulpit jest aktywny. Przy
zablokowanej sesji zlecenia czekają w kolejce. Aby agent pobierał potwierdzenia,
uzupełnij również `confirmation_download` w jego lokalnym profilu.

## Rejestracja i próba

Zainstaluj **Biuro-Background-Worker** z ustawień workera albo uruchom
`scripts\install_background_worker.cmd`. Ta sama operacja rejestruje
**Biuro-Platnik-Worker** jako osobne zadanie interaktywne przy logowaniu i
uruchamia je w bieżącej sesji. Główny worker działa także bez zalogowanego
użytkownika; agent GUI wymaga aktywnego, odblokowanego pulpitu.

W **Ustawienia → Płatnik** kliknij **Test agenta GUI krok po kroku (bez importu)**.
Test pokazuje osobno walidację profilu, dostęp do kolejki PostgreSQL, pulpit,
okno Płatnika i skonfigurowane tabele. Może uruchomić Płatnika, jeśli jest
zamknięty. Nie pobiera zadań z kolejki, nie odczytuje danych pracowników i nie
importuje wyników.

Wybierz w **Kadry → Sprawdzanie → Raport → Składki ZUS (miesięcznie)**
okres z już zaimportowaną deklaracją i kliknij **Sprawdź wysyłkę w Płatniku**.
Bez zaznaczenia Biuro zleca sprawdzenie wszystkich dostępnych dla użytkownika
firm z prawidłowym NIP-em płatnika. Zaznaczenie wierszy ogranicza zlecenie do
tych firm. Do kolejki trafiają wyłącznie płatnicy powiązani z firmami w Biuro.
Agent porównuje NIP w rejestrze i pobiera szczegóły tylko pasujących wierszy.
Porównaj zestaw i potwierdzenie z rejestrem
Płatnika. Sprzeczne wpisy dla tej samej deklaracji kończą odczyt błędem.
Status potwierdzenia wymaga identyfikatora potwierdzenia. Wynik bez
wpisu oznacza, że agent w odczytanym widoku nie znalazł zgodnego zestawu.
Domyślna kolumna raportu zachowuje stan deklaracji z Płatnika.

Plik `agent.log` obok profilu zawiera identyfikator zadania i wynik techniczny.
Test wdrożeniowy powinien
objąć zestaw potwierdzony, wysłany i zweryfikowany przypadek bez wpisu, a
następnie ponowny odczyt po odblokowaniu sesji. Dla profilu z pobieraniem
użyj zestawu wysłanego z ważnym identyfikatorem. Sprawdź, czy Płatnik
zarejestrował pobrany dokument i Biuro wyświetliło nowy stan.
