# Saldeo — historia powiadomień w workerze (1.5.1)

Jedna instalka Biuro nadal instaluje program i opcjonalny worker; domyślnie tylko
program. Aktualizacja wymaga jednorazowej migracji bazy (na stanowisku Biuro albo
skrótem „Biuro - aktualizacja bazy workera” używającym worker.env).

W „Biuro Worker” otwórz Saldeo. Ustaw zainstalowaną na serwerze przeglądarkę
Microsoft Edge lub Google Chrome. Biblioteka sterująca jest w EXE; nie wymaga
Pythona ani dodatkowego ręcznego instalowania pakietów. Przeglądarka nie jest
pobierana przez instalator. Jeżeli serwer jej nie ma, zainstaluj Edge/Chrome.

## Uruchomienie na rzeczywistym koncie

1. Zapisz adres logowania i otwórz przeglądarkę przyciskiem. W osobnym procesie
   zaloguj się jako administrator Biuro, następnie zaloguj do Saldeo. Wykonaj
   ewentualne 2FA i zamknij przeglądarkę. Używaj konta Windows workera na serwerze;
   logowanie na innym PC nie przekazuje sesji na serwer.
2. Zostaw domyślny adres tabeli **Lista operacji**. W tym widoku wbudowane są
   znaczniki sesji, wierszy i górnego przycisku „Następna strona”. Pole
   „Otwórz dzwonek/listę” pozostaw puste dla pełnej tabeli. Własny widok
   historii wymaga odpowiadających mu selektorów.
3. Aby włączyć automatyczne ponowne logowanie, zapisz login i hasło w panelu
   Saldeo workera. Dla standardowego formularza selektory są wbudowane.
   Po wygaśnięciu sesji worker przy następnym odczycie podejmuje jedną próbę
   logowania, a po sukcesie ponownie otwiera historię. Hasło jest szyfrowane
   wspólnym kluczem Biuro. Formularz logowania musi działać pod tym samym
   origin HTTPS. Jeśli pojawi się dodatkowy etap logowania, panel zgłasza
   „Wymagane logowanie / 2FA”.
4. Włącz monitoring, zapisz i wybierz „Sprawdź i zapisz powiadomienia teraz”.
   Porównaj wynik z historią Saldeo, w tym drugą stronę, pustą listę i kilka firm.
5. W Zadaniach workera włącz Saldeo. Domyślny odstęp to 300 s po zakończeniu
   poprzedniego przebiegu, przerwa między stronami 2 s, maksymalnie 20 stron.

## Działanie i ograniczenia

To odczyt historii stanowiącej źródło push, nie przechwytywanie toastów Windows
ani stały odbiornik Web Push. Worker okresowo otwiera prywatny profil w tle,
odczytuje historię i zamyka przeglądarkę. Profil przetrwa restart serwera. Przy
ręcznym logowaniu blokada PostgreSQL zapobiega równoczesnemu użyciu profilu.
Profil zawiera prywatne cookies — nie kopiuj go do repozytorium ani publicznego
katalogu. Logi nie zapisują haseł, cookies ani błędów przeglądarki zawierających URL.

Brak przycisku stronicowania w konfiguracji, osiągnięcie limitu stron/5000 wpisów
lub zmiana DOM dają wynik niepełny/błąd, a nie potwierdzenie nadrobienia całej
historii. Nadrabiamy tylko wpisy nadal przechowywane w Saldeo. Jeżeli historia
wymaga dodatkowego otwarcia dzwonka lub filtrów, adapter wymaga dopasowania;
przycisk paginacji musi być selektorem Next, także gdy jest wyłączony.

Zapisana nazwa `nazwa_saldeo_firmy` jest porównywana z powiadomieniem bez różnic
w wielkości liter i odstępach; kropki są zachowane. Obsługiwane przykłady:
„MERIT. informuje o dostarczeniu dokumentów za 10/2026” i „Klient (MERIT.)
informuje o dostarczeniu kompletu dokumentów za 10/2026”. Brak lub powielona
nazwa oznacza „Do przypisania”; po poprawieniu mapowania worker ponawia wpis.

Każdy wpis ma trwały klucz. Duplikaty są pomijane; nieudana transakcja pozostawia
zdarzenia do ponowienia. Status kompletu i potwierdzenie obsługi zapisują się
atomowo. Nie są zmieniane ręczne liczniki ani etap „Wysłane wyniki”. Data dostarczenia
jest datą wykrycia w Biuro (nie udajemy, że jest datą zgłoszenia klienta);
oryginalny czas jest osobną kolumną, jeżeli strona podaje datę zakończenia
lub udostępnia element time.

Panel Saldeo zawiera historię ostatnich 500 zdarzeń (baza zachowuje wszystkie).
Zwykli pracownicy widzą zdarzenia firm zgodnie z uprawnieniami; nierozpoznane
wpisy widzi administrator. Menu Powiadomienia grupuje zapisane w jednym przebiegu
zgłoszenia, a przeczytanie komunikatu nie kasuje historii. To powiadomienia w
aplikacji, nie wysyłka e-mail. Stary watcher Windows nie startuje przy włączonym
monitoringu web; jego błędy zapisu nie oznaczają już zdarzenia jako obsłużonego.

Testy integracyjne i instalator są weryfikowane przez GitHub CI. Wbudowane selektory potwierdzono na zapisanej stronie. Pierwszy odczyt
na koncie sprawdza działanie całego połączenia.

Potwierdzony adres historii:
https://saldeo.brainshare.pl/biuro/operacje/lista-operacji.hta
Jest domyślnie wpisany w ustawieniach. Wbudowany profil odczytuje tabelę z tego adresu.

## Wykrywanie przeglądarki i formularz logowania

W ustawieniach można wybrać automatyczne wykrywanie, Chrome lub Edge albo wskazać
pełną ścieżkę do istniejącego pliku EXE. Wykrywanie uwzględnia rejestr App Paths
oraz instalacje w Program Files, Program Files (x86) i LocalAppData bieżącego
konta Windows. „Sprawdź wykrywanie przeglądarki” pokazuje konkretną ścieżkę.
Playwright dostaje tę ścieżkę zamiast polegać wyłącznie na domyślnym kanale.

Otwarcie ręcznego logowania nie wymaga selektorów monitorowania, także jeśli
w konfiguracji włączono wcześniej monitoring. Gdy formularz zawiera po jednym
widocznym polu `input[qa-id="username"]`, `input[qa-id="password"]` i przycisku
`button[qa-id="login-button"]`, domyślne selektory działają niezależnie od
języka formularza. Starszy domyślny adres i selektor przycisku są aktualizowane
przy odczycie konfiguracji standardowego formularza Saldeo.
Nie odczytujemy wartości wpisanych do formularza ani hasła. Pozostałe mapowania
są zachowane; po zamknięciu procesu logowania panel wczytuje rozpoznane pola do
pustych komórek, zachowując własne wpisy użytkownika.

## Pełna Lista operacji

Zapis strony Saldeo potwierdził wiersze
`bs-mat-table.background-operation-list-table .mat-row` oraz dwa przyciski
„Następna strona”. Worker używa górnego przycisku, w obrębie listy operacji,
także do potwierdzenia końca stronicowania po jego wyłączeniu. Zniknięcie
przycisku po zmianie układu daje wynik niepełny.

Worker odczytuje tylko wiersze typu „Komplet dokumentów” ze statusem
„Zakończone”. Nazwę firmy bierze z kolumny „Nazwa pliku / firma”, okres
z „Uwagi”, a czas źródłowy z „Data zakończenia”. Inne ukończone operacje
nie oznaczają dostarczenia dokumentów. Próbka zawierała wiersze historii.
Selektor pustego widoku pozostaje opcjonalny i wymaga osobnego potwierdzenia.

Starsze domyślne ustawienia dzwonka na standardowym adresie są aktualizowane
przy odczycie ustawień. Własne selektory użytkownika są zachowane. Własny
widok dzwonka może nadal używać `div[qa-id^="row-bo-id-"]` jako selektora karty.

Numer row-bo-id-0 opisuje pozycję, a nie tożsamość zdarzenia. Etykieta godziny
może zmienić się przy kolejnym odczycie, więc potwierdzenia kompletu deduplikujemy
po firmie i okresie w obrębie źródła. Powtórne zgłoszenie tego samego kompletu
w tym samym miesiącu nie tworzy nowego powiadomienia w Biuro. Godziny „09:13”
nie zamieniamy na wymyśloną pełną datę źródłową.
