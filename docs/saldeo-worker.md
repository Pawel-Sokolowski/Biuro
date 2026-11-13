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
2. Wskaż adres **pełnej historii**, obejmującej przeczytane i nieprzeczytane wpisy.
   Potrzebne są selektory CSS rzeczywistego widoku Saldeo: znacznik zalogowania,
   pojedynczy wpis, komunikat pustej listy, przycisk kolejnej strony. Nie znamy
   prywatnego DOM Waszego konta, dlatego nie dostarczamy zgadywanych selektorów.
   Wybierz je przez narzędzia deweloperskie Edge/Chrome lub zleć konfigurację.
3. Opcjonalnie podaj login, hasło i selektory formularza dla automatycznego
   ponownego logowania. Hasło jest szyfrowane wspólnym kluczem Biuro. 2FA/CAPTCHA
   wymagają ręcznego otwarcia przeglądarki; nie są obchodzone. Automatyczne
   logowanie obsługuje formularz na tym samym origin HTTPS, nie zewnętrzne SSO.
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
oryginalny czas jest osobną kolumną, jeżeli strona udostępnia element time.

Panel Saldeo zawiera historię ostatnich 500 zdarzeń (baza zachowuje wszystkie).
Zwykli pracownicy widzą zdarzenia firm zgodnie z uprawnieniami; nierozpoznane
wpisy widzi administrator. Menu Powiadomienia grupuje zapisane w jednym przebiegu
zgłoszenia, a przeczytanie komunikatu nie kasuje historii. To powiadomienia w
aplikacji, nie wysyłka e-mail. Stary watcher Windows nie startuje przy włączonym
monitoringu web; jego błędy zapisu nie oznaczają już zdarzenia jako obsłużonego.

Testy integracyjne i instalator są weryfikowane przez GitHub CI. Faktyczny odczyt
Saldeo i selektory wymagają sprawdzenia na zalogowanym koncie; bez tego konfiguracja
pozostaje wyłączona i system nie deklaruje skutecznego monitorowania.

Potwierdzony adres historii:
https://saldeo.brainshare.pl/biuro/operacje/lista-operacji.hta
Jest domyślnie wpisany w ustawieniach. Jeśli lista wymaga kliknięcia dzwonka,
można podać opcjonalny selektor „otwórz dzwonek/listę”. Sama znajomość adresu
nie potwierdza selektorów wpisów w prywatnym widoku.

## Wykrywanie przeglądarki i formularz logowania

W ustawieniach można wybrać automatyczne wykrywanie, Chrome lub Edge albo wskazać
pełną ścieżkę do istniejącego pliku EXE. Wykrywanie uwzględnia rejestr App Paths
oraz instalacje w Program Files, Program Files (x86) i LocalAppData bieżącego
konta Windows. „Sprawdź wykrywanie przeglądarki” pokazuje konkretną ścieżkę.
Playwright dostaje tę ścieżkę zamiast polegać wyłącznie na domyślnym kanale.

Otwarcie ręcznego logowania nie wymaga selektorów monitorowania, także jeśli
w konfiguracji włączono wcześniej monitoring. Gdy formularz zawiera po jednym
widocznym polu `input[qa-id="username"]`, `input[qa-id="password"]` i przycisku
`button:has-text("Zaloguj")`, zapamiętujemy te selektory w pustych ustawieniach.
Nie odczytujemy wartości wpisanych do formularza ani hasła. Pozostałe mapowania
są zachowane; po zamknięciu procesu logowania panel wczytuje rozpoznane pola do
pustych komórek, zachowując własne wpisy użytkownika.

To rozpoznanie formularza logowania, nie automatyczne rozpoznanie struktury
historii powiadomień. Do historii nadal trzeba dopasować znacznik sesji,
wiersze, pustą listę i paginację. Pełnego znacznika HTML nie wkleja się do pola CSS:
z przesłanego `<input ... qa-id="username" id="mat-input-0">` selektor to
`input[qa-id="username"]`. Dynamicznego `mat-input-0` nie utrwalamy.


Zweryfikowane fragmenty formularza przesłane przez użytkownika:
- pole loginu: `input[qa-id="username"]`;
- pole hasła: `input[qa-id="password"]`;
- ikona dzwonka: `[qa-id="bo-counter"]` (kliknięcie, jeśli potrzebne do otwarcia listy).

Elementy `bs-mat-cell` ze statusem `background-operation-list-table__status` /
„Zakończone” opisują komórki operacji w tle. Nie są potwierdzeniem kompletu
dokumentów. Selektor pojedynczego wpisu musi obejmować cały komunikat z nazwą
firmy i okresem, np. „MERIT. informuje o dostarczeniu dokumentów za 10/2026”.
Nie konfigurujemy automatycznie selektora historii na podstawie samych tych
komórek ani nie zmieniamy reguły oznaczania kompletu na podstawie słowa „Zakończone”.

## Potwierdzony format karty dzwonka

Nowy fragment użytkownika to pełna karta:
`div[qa-id^="row-bo-id-"]`, z tytułem `.content-col .font-14`
„BWSERVIC - Komplet dokumentów” i okresem `.content-col .font-12-grey`
„Październik 2026”. Używamy tych struktur, rozpoznajemy polskie nazwy miesięcy.
Samo „done”/„Zakończone” nadal niczego nie potwierdza.

Preset formularza i dzwonka jest teraz dostępny domyślnie (uzupełnia także puste
stare ustawienia bez nadpisywania własnego selektora wpisów). Brak selektora pustej
listy nie blokuje odczytu widocznych kart, ale pusta lista nie jest wtedy uznawana
za potwierdzony brak zdarzeń. Brak paginacji nadal daje wynik **niepełny**:
dzwonek może pokazywać tylko ostatnie wpisy, nie udajemy nadrobienia pełnej historii.
Potrzebne są jeszcze szczegóły pełnej listy/stronicowania oraz przycisku logowania.

Numer row-bo-id-0 opisuje pozycję, a nie tożsamość zdarzenia. Etykieta godziny
może zmienić się przy kolejnym odczycie, więc potwierdzenia kompletu deduplikujemy
po firmie i okresie w obrębie źródła. Powtórne zgłoszenie tego samego kompletu
w tym samym miesiącu nie tworzy nowego powiadomienia w Biuro. Godziny „09:13”
nie zamieniamy na wymyśloną pełną datę źródłową.
