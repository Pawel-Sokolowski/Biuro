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

## Koniec dnia na komputerze kadr

### Wysyłka i potwierdzenia

1. Na komputerze kadr zapisz PIN przez **Ustawienia → Płatnik → PIN
   certyfikatu Płatnika…**. To poświadczenie bieżącego konta Windows.
   Przy wymianie certyfikatu zapisz PIN nowego certyfikatu lub usuń wpis.
2. W Biuro wybierz **Kadry → Sprawdzanie → Składki ZUS (miesięcznie) →
   Płatnik — koniec dnia… → Wyślij i pobierz UPO…**.
3. W Płatniku otwórz **Narzędzia → Wyślij za wielu płatników**, przejdź do
   wyboru zestawów i wskaż dokumenty. Po zatwierdzeniu listy, kiedy przycisk
   **Dalej** jest aktywny, kliknij w Biuro **Wyślij wybrane zestawy do ZUS**.
   Agent kontynuuje sprawdzanie, podpisanie i przekazanie wskazanych zestawów
   przez kreator Płatnika. Jeśli Płatnik wyświetli komunikat wymagający decyzji,
   obsłuż go w Płatniku i kliknij **Wznów otwarty kreator**. Ponowne żądanie
   PIN-u na tym samym etapie wymaga sprawdzenia przez operatora.
4. Po wysyłce sprawdź wynik w Płatniku. Otwórz **Narzędzia → Pobierz za wielu
   płatników**, wybierz wysłane zestawy i zatwierdź listę. W Biuro kliknij
   **Pobierz i zarejestruj potwierdzenia**. Po zamknięciu kreatora przez agenta
   Biuro odczyta statusy i UPO przez skonfigurowane połączenie SQL. Jeśli ZUS
   udostępni potwierdzenie później, uruchom pobieranie potwierdzeń ponownie.

Wysyłka dotyczy zestawów zaznaczonych w Płatniku. Zaznaczenie firm w Biuro
określa zakres odczytu SQL. Do obsługi kreatorów wystarcza działające okno
Płatnika. Profil tabeli jest potrzebny w osobnym trybie odczytu rejestru.
[Instrukcja ZUS, rozdziały 5.6.14.1 i 5.6.14.2](https://pobierz.zus.pl/dystrybucja/a1_10_02_002/dokumentacja/Dok_Uzytk_A1_10.02.002_wersja_7.8.pdf)

### Odczyt po wysyłce

1. Na komputerze kadr otwórz Płatnika i zakończ pracę dla właściwych płatników.
   Jeśli wysyłkę wykonano bezpośrednio w Płatniku, sprawdź raport wysyłki.
2. Dla wysłanych zestawów użyj **Narzędzia → Pobierz i rejestruj
   potwierdzenie**. Sprawdź wynik kreatora w Płatniku. Jeśli chcesz powierzyć
   ten krok agentowi, skonfiguruj i przetestuj osobny widok zestawów wysłanych.
3. W Biuro otwórz **Kadry → Sprawdzanie → Składki ZUS (miesięcznie) →
   Płatnik — koniec dnia…**. Uruchom raz pod koniec dnia. Odczyt obejmuje
   bieżący i poprzedni miesiąc według czasu polskiego oraz zaznaczone firmy.
   Przy braku zaznaczenia obejmuje firmy dostępne w Biuro dla użytkownika.
4. Po ręcznym zarejestrowaniu potwierdzeń wybierz **Pobierz wyniki przez SQL**.
   Biuro odczyta statusy i UPO z bazy Płatnika używając zapisanego połączenia SQL.
   Przy sprawdzonym profilu wybierz **Uruchom agenta GUI**, aby wykonać
   lokalny odczyt na odblokowanym pulpicie. Wyniki GUI są w kolumnie
   **Agent Płatnika**. Odczyt SQL aktualizuje raport deklaracji.

Agent kończy pracę po opróżnieniu kolejki. Ponowne uruchomienie tego samego
dnia pozwala dokończyć zaległe lub nieudane odczyty. Wyniki GUI już zapisane
tego dnia nie są ponownie zlecane.

Przy przejściu ze starej wersji zatrzymaj i wyłącz zadania
`Biuro-Platnik-Worker` oraz `Biuro-Platnik-GUI-Agent`, jeśli są zarejestrowane
na serwerze lub komputerze kadr. Zaktualizuj serwer Biuro i komputer kadr.
Główny `Biuro-Background-Worker` nadal obsługuje zadania serwerowe.

## Baza i konto

1. Zaktualizuj serwer Biuro i wykonaj migrację `20261121_08` poleceniem
   `python migrate_db.py`.
2. Przygotuj osobny login PostgreSQL na potrzeby agenta. Konto właściciela
   schematu nadaje dostęp do dwóch funkcji kolejki. Login agenta powinien mieć
   możliwość połączenia z bazą oraz uprawnienia z poniższego skryptu.
3. Na komputerze kadr administrator Biuro może wpisać adres, port, bazę
   i dedykowany login PostgreSQL w **Ustawienia → Płatnik**. Dane muszą
   wskazywać tę samą bazę, z której korzysta zalogowane Biuro.
   Na koncie Windows agenta użyj usługi libpq
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

Aby agent pobierał potwierdzenia podczas **Płatnik — koniec dnia…**,
dodaj do lokalnego profilu sekcję `confirmation_download`.
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

## Test profilu i instalacji

Na komputerze kadr wybierz w **Ustawienia → Płatnik** przycisk
**Test agenta GUI krok po kroku (bez importu)**. Sprawdza profil, dostęp do
kolejki PostgreSQL, aktywny pulpit, okno Płatnika i widoki. Do sprawdzenia
dostępu do komórek użyj też **Sprawdź odczyt tabeli** przy otwartym rejestrze.
Dla `TdxDBGrid` bez obsługi UIA Grid i Table użyj ręcznego kreatora oraz
przycisku **Pobierz wyniki przez SQL**.

Po pierwszym przebiegu porównaj zestaw i UPO dla jednej firmy z rejestrem
Płatnika. Plik `agent.log` obok profilu zawiera identyfikator zadania i wynik
techniczny. Opcjonalnie instalacja źródłowa może przetworzyć już zapisaną
kolejkę poleceniem:

```powershell
& $agentPython -m app.integrations.platnik.gui_agent daily --config C:\Biuro\platnik-agent.json
```
