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
W Enovie ten sam limit obejmuje wyszukiwanie baz i odczyt dokumentów.

W **Odczyt z Enovy** kliknij **Znajdź bazy Enovy po NIP**. Poniżej jest przycisk
**Nierozpoznane bazy i firmy — ręczne powiązania**. Okno pokazuje każdą widoczną
bazę, odczytane NIP-y i powód braku dopasowania (np. brak firmy, kilka kopii bazy,
brak metadanych lub błąd SQL). Osobna podzakładka pokazuje firmy bez bazy. Wybierz
bazę oraz firmę i kliknij **Powiąż**, zatwierdź okno i **Zapisz połączenie Enova**.
Można też wpisać nazwę bazy ręcznie. Jednej bazy nie można przypisać do dwóch firm;
zastąpienie istniejącej bazy wymaga potwierdzenia. Ręczne mapowanie nie omija
kontroli zgodności NIP-u danych podczas importu. Bazy niewidoczne dla loginu SQL
nie mogą pojawić się na liście; przy niepełnym odczycie automatyczne dopasowanie
pozostaje wstrzymane.

Podaj **Rok jednorazowego importu** i kliknij **Importuj jednorazowo liczbę
dokumentów — cały rok do Sprawdzania**. Przycisk zapisuje zmienione pola połączenia
i powiązania, następnie pobiera styczeń–grudzień dla wszystkich przypisanych firm.
Działa również przy wyłączonym codziennym imporcie. Widać postęp oraz wynik każdej
bazy, liczby dla miesięcy i ewentualne błędy. Błąd jednej firmy nie przerywa
następnych; niepełne lub niezgodne dane nie są zapisywane jako zera. Wynik pozostaje
w historii importów. Ponowne kliknięcie odświeża dane, nie dodaje ich do poprzednich.

Import roczny zapisuje tylko rodzaje dokumentów księgowych w **Sprawdzaniu**.
Nie zmienia widoku miesięcznego, ręcznych liczników, etapów, faktur, deklaracji/UPO
ani danych kadrowych. Zwykły import licznika do widoku miesięcznego obejmuje tylko
okresy od miesiąca aktywacji tej konfiguracji (granica zapisywana przy pierwszym
zapisie połączenia); starsze miesiące pozostają do ręcznej obsługi. Nadal wymaga
etapu **Wysłane wyniki** i zachowuje ręczne wpisy, również zmienione już po imporcie.
Roczny odczyt nie uruchamia dodatkowego workera ani nie zmienia harmonogramu.

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
