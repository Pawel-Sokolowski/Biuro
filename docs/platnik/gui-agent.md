# Agent Płatnika w interaktywnej sesji Windows

Agent odczytuje status już istniejących zestawów DRA i RCA oraz identyfikator
elektronicznego potwierdzenia, jeśli jest widoczny w Płatniku. W Biuro wyniki są
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
   Etykiety w przykładzie służą do zilustrowania formatu.
3. W Płatniku otwórz rejestr dokumentów lub zestawów z widocznymi polami
   NIP-u, miesiąca, formularza, numeru deklaracji, identyfikatora zestawu,
   stanu i potwierdzenia. Numer deklaracji jest częścią identyfikatora
   rozliczenia, niezależną od numeru zestawu. Dla etykiety `sent` potrzebna
   jest również kolumna z datą i godziną wysyłki.
4. Polecenie `describe` pokazuje typ i identyfikator widocznych kontrolek.
   Uzupełnij identyfikator rejestru, pozycje nagłówków od zera oraz dokładne
   wartości stanów. Agent sprawdza nagłówki podczas każdego odczytu.

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

## Rejestracja i próba

Uruchom z katalogu repozytorium w PowerShell na koncie agenta. Pierwsze
polecenie sprawdza profil, pakiety i uprawnienia kolejki. Drugie rejestruje
zadanie i uruchamia je w bieżącej sesji.

```powershell
.\scripts\install_platnik_gui_agent.ps1 -Config C:\Biuro\platnik-agent.json -CheckOnly
.\scripts\install_platnik_gui_agent.ps1 -Config C:\Biuro\platnik-agent.json -Start
```

Wybierz w **Kadry → Sprawdzanie → Raport → Składki ZUS (miesięcznie)**
okres z już zaimportowaną deklaracją, zaznacz firmę i kliknij
**Sprawdź wysyłkę w Płatniku**. Porównaj zestaw i potwierdzenie z rejestrem
Płatnika. Sprzeczne wpisy dla tej samej deklaracji kończą odczyt błędem.
Status potwierdzenia wymaga identyfikatora potwierdzenia. Wynik bez
wpisu oznacza, że agent w odczytanym widoku nie znalazł zgodnego zestawu.
Domyślna kolumna raportu zachowuje stan deklaracji z Płatnika.

Na czas próby można zatrzymać zadanie Harmonogramu i uruchomić
`gui_agent once` z tym samym `--config`. Plik `agent.log` obok profilu
zawiera identyfikator zadania i wynik techniczny. Test wdrożeniowy powinien
objąć zestaw potwierdzony, wysłany i zweryfikowany przypadek bez wpisu, a
następnie ponowny odczyt po odblokowaniu sesji.
