# Konfiguracja i aktualizacja workera — 1.4.6

Połączenia Enovy/Płatnika i ustawienia zadań są zapisane w PostgreSQL Biuro.
Są wspólne dla panelu workera i aplikacji pracowników. Przed instalacją autostartu
panel zapisuje zmiany z otwartych zakładek **Odczyt z Enovy**, **Płatnik** i **Zadania**.
Nie otwiera nieużywanych formularzy ani nie zastępuje ich ustawień pustymi wartościami.
Błąd walidacji kieruje do odpowiedniej zakładki i blokuje instalację.
Pozostałe usługi zapisuj ich własnymi przyciskami.

W zakładce Instalacja jest też przycisk **Zapisz połączenia SQL i ustawienia zadań**.
Połączenie Enovy można zapisać przed wyszukiwaniem/przypisaniem baz firm. Import
bez przypisanych firm zgłasza brak konfiguracji i nie oznacza dnia jako pobranego.
Hasła SQL pozostają zaszyfrowane; puste pole hasła po ponownym otwarciu jest normalne.
Adres serwera, baza, użytkownik i limit powinny być ponownie widoczne.

Zakładki odczytują ponownie zapisane dane, gdy do nich wracasz, również w programie
Biuro. Niezapisane lokalne zmiany pozostają zachowane. W nagłówku ustawień programu
i w zakładce Instalacja workera sprawdzisz rzeczywisty adres PostgreSQL, nazwę bazy
i schemat, bez loginu/hasła. Sam wspólny adres serwera nie oznacza tej samej bazy.
`worker.env` jest konfiguracją PostgreSQL/klucza konta Windows; nie zastępuje
połączeń Enovy/Płatnika zapisanych w bazie Biuro.

W obu formularzach SQL można wpisać limit od **30 do 1200 sekund**. Domyślne
120 sekund dotyczy pojedynczego zapytania, nie całej operacji.

Otwarcie **Biuro Worker** nie uruchamia drugiego procesu. Panel odczytuje heartbeat
workera tej bazy i pokazuje jego host, PID, identyfikator wersji, czas uruchomienia,
ścieżkę EXE, plik konfiguracji i czas odczytu harmonogramu. Tytuł okna zawiera wersję
panelu; aktywny worker może mieć inną wersję do czasu restartu. Rozpoczęte zadania
kończą się ze swoim dotychczasowym zestawem ustawień; nowe używają zapisanych danych.

Instalator EXE podmienia pliki w wybranej lokalizacji. Ponowne **Zainstaluj /
uruchom ponownie worker** dla tego samego konta i ścieżki zatrzymuje stare zadanie,
czeka na jego zatrzymanie i uruchamia ten sam wpis Harmonogramu Windows z nowym EXE.
Przed tą operacją zakończ bieżące wysyłki; po przerwaniu sprawdź ich wynik przed
ponawianiem. Zadanie innej instalacji/konta nie jest nadpisywane. Samo otwarcie
panelu i zapis konfiguracji nie wymagają reinstalacji ani restartu workera.
