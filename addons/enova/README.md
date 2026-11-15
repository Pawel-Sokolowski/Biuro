# Biuro e-ZLA — enova365 2512.11.13

Dodatek pobiera e-ZLA zapisane przez worker Biuro i tworzy nieobecności przez oficjalny model biznesowy Sonety. Biblioteki są przypięte do **2512.11.13 / .NET 8**. Paczka z GitHub Actions zawiera wyłącznie `Biuro.Ezla.dll`, `Biuro.Ezla.UI.dll` i tę instrukcję. Nie zawiera bibliotek uruchomieniowych Sonety, schematów klientów, konfiguracji ani kluczy.

Wymaga serwera HTTPS Biuro z endpointem `/api/v1/enova/ezla` (zmiany usługi serwerowej z PR #102 i dodatek z tego PR), skonfigurowanego pobierania e-ZLA w workerze Biuro i konta Biuro z dostępem do Sprawdzania oraz prawem eksportu danych. Klucz szyfrujący, dane ZUS i dane SQL pozostają na serwerze Biuro. Dodatek otrzymuje wyłącznie uwierzytelnioną, czasową sesję oraz raporty wskazanej firmy.

## Instalacja i podgląd

1. Pobierz artefakt `Biuro.Ezla-enova-2512.11.13` z zielonego przebiegu GitHub Actions. Umieść obie DLL w katalogu dodatków używanym przez instalację enova. Zainstaluj je także przy SonetaServer/HZ. Uruchom ponownie odpowiednie procesy, aby załadowały DLL.
2. Otwórz firmę enova. Jej NIP w pieczątce firmy musi jednoznacznie odpowiadać firmie w Biuro. Spółka cywilna ma **jedną bazę enova**. Indywidualne NIP-y wspólników identyfikują płatnika raportu, a nie osobne bazy JDG. Wszystkie zwolnienia wspólników są pobierane pod NIP-em spółki i trafiają do jej otwartej bazy; nie trzeba tworzyć firm ani baz indywidualnych. NIP jest odczytywany z enova, nie wpisywany swobodnie w oknie importu. Biuro weryfikuje także NIP płatnika, w tym indywidualny NIP wspólnika zapisany przy spółce cywilnej.
3. Na liście pracowników wybierz **Czynności → Biuro → e-ZLA — pobierz i sprawdź**. Podaj adres `https://serwer:8443`, e-mail i hasło Biuro, zakres miesięcy i istniejącą definicję nieobecności. Domyślnie **Tylko podgląd** jest włączone. Hasło nie jest zapisywane w ustawieniach operatora ani bazie enova.
4. Sprawdź wyniki. Aby zapisać nieobecności, uruchom ponownie z wyłączonym podglądem. Dodatek ponownie pobierze raporty i zweryfikuje stan bazy w nowej transakcji. Nie zapisuje innych zmian z otwartego formularza.

Certyfikat HTTPS musi być zaufany przez system konta uruchamiającego enova/HZ. Dodatek nie wyłącza weryfikacji certyfikatu i nie podąża za przekierowaniami z hasłem/sesją. Certyfikat publiczny urzędu certyfikacji nie jest kluczem szyfrującym Biuro.

## Harmonogram Zadań

Licencja HZ i działająca usługa SonetaServer pozwalają uruchamiać import bez otwartego pulpitu.

1. Na komputerze usługi HZ skonfiguruj lokalne zmienne środowiska dla jej tożsamości: `BIURO_EZLA_URL`, `BIURO_EZLA_EMAIL`, `BIURO_EZLA_PASSWORD`; opcjonalnie `BIURO_EZLA_DEFINITION` (domyślnie `Zwolnienie chorobowe`). Użyj osobnego, ograniczonego konta Biuro. Nie wpisuj hasła do definicji zadania, repozytorium ani skryptu przesyłanego pocztą. Zmiana/dezaktywacja konta odbiera dostęp. Zrestartuj usługę po zmianie jej środowiska. Administrator systemu tego hosta może odczytać jego lokalne poświadczenia.
2. Jako administrator enova wybierz **Czynności → Biuro → e-ZLA — zainstaluj harmonogram**. Wskaż nazwę **jednej istniejącej instancji HZ** i godzinę, np. 06:00. Instalator tworzy zadanie globalne z `[AutoAction]` i dzienny harmonogram od następnego dnia, początkowo wyłączony. Nie zmienia istniejącego zadania przy ponownym uruchomieniu instalatora.
3. Po udanym podglądzie włącz utworzony harmonogram w Opcjach enova. Usługa i jej operator muszą mieć uprawnienia do odczytu pracowników oraz dodawania nieobecności. Jedna wskazana instancja HZ zapobiega zbędnym równoległym pobraniom. Blokada systemowa obejmuje jeden host; stałe GUID-y nieobecności dają dodatkową ochronę przed równoczesnym dodaniem tego samego odcinka z dwóch hostów (konflikt zapisu wymaga ponowienia).
4. Ustaw pobieranie raportów workera Biuro wcześniej niż import HZ. HZ przetwarza bieżący i dwa poprzednie miesiące oraz raporty wymagające wyjaśnienia. Dla starszych zaległości użyj importu ręcznego. Raporty są stronicowane, sumy SHA-256 weryfikowane, całość jest pobierana przed zmianami w enova.

Punkt wejścia dla własnej istniejącej definicji HZ: `Biuro.Ezla.EzlaJob.Run(Session)`. Definicja utworzona przez instalator zawiera wyłącznie wywołanie tej metody. Wymaga zweryfikowania uruchomienia w używanej instalacji SonetaServer; CI nie zastępuje próby na rzeczywistej bazie enova i posiadanych licencjach.

## Reguły importu i ograniczenia

- Dopasowanie po PESEL przez indeks Sonety. Brak lub wiele kartotek oznacza sprawdzenie ręczne; nazwisko nie jest używane do zgadywania. Osoby z paszportem i właściciele bez kartoteki pracownika wymagają obsługi w enova.
- Importuje wystawione zwolnienia chorobowe, kody A–E, datę wystawienia oraz odcinki pobytu w szpitalu. Stosuje konwencję standardowego importu PUE Sonety 2512: data dostarczenia odpowiada dacie wystawienia, a pole ZLA.Data zawiera datę wygenerowania raportu. Kody B/C/D/E zachowują właściwą przyczynę; natywny setter kodu A ustawia kontynuację okresu zasiłkowego. Źródło XML jest zapisywane przy nieobecności **wyłącznie dla tego dokumentu**, nie całego raportu z innymi pracownikami.
- Opieka, anulowania, korekty, e-ZLA/K, zwolnienia wsteczne, nieznane statusy/kody i konflikty istniejących nieobecności są pokazywane do sprawdzenia; dodatek ich samodzielnie nie nadpisuje ani nie usuwa. Przed dodaniem uwzględnia statusy tego samego dokumentu ze wszystkich pobranych raportów. Konflikt dwóch wystawionych wersji przerywa zapis całej partii.
- Ponowny import zgodnych nieobecności jest pomijany. Zmieniony okres tego samego numeru i nakładające się inne nieobecności nie są nadpisywane. Zapis odbywa się w transakcji; błędy i konflikty zapisu są przekazywane do HZ/operatora.
- Log zawiera liczniki raportów, dokumentów i dodanych odcinków, bez PESEL, treści medycznej, haseł i tokenów. Szczegóły decyzji są dostępne w podglądzie ręcznym. Błędów importu nie wolno traktować jako potwierdzenia pobrania nowych danych z ZUS.

## Weryfikacja

Build oraz testy parsera/reguł są uruchamiane **wyłącznie w GitHub Actions**. Testy używają syntetycznych raportów; nie używają prywatnych schematów ani bazy klienta. Dodatkowe testy usługi Biuro sprawdzają uwierzytelnienie, uprawnienia, izolację firm, integralność raportów i statusy anulowania. Operacje na rzeczywistej bazie, konfiguracja definicji zwolnień i wykonanie HZ wymagają odbioru na instalacji enova 2512.11.13.

Źródła: [oficjalne wskazówki programistyczne Soneta](https://github.com/soneta/soneta-erp-skills/blob/main/skills/programming/SKILL.md), [bezpieczny kod](https://github.com/soneta/soneta-erp-skills/blob/main/skills/programming/references/safe-code.md), [harmonogram](https://github.com/soneta/soneta-erp-skills/blob/main/skills/programming/references/domeny/workflow/WORKFLOW09-automatyzacja.md), [biblioteki 2512.11.13](https://www.nuget.org/packages/Soneta.Products.Modules/2512.11.13).
