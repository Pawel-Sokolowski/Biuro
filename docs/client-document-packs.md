# Komplety dokumentów klienta

W zakładce **Klienci** zaznacz firmę i wybierz **Komplet dokumentów…**.
Sprawdź pełną nazwę, adres, identyfikatory, daty, reprezentantów, zakres
obsługi i parametry usługi. Zmiany w tym oknie dotyczą wyłącznie generowanego
kompletu, nie zmieniają kartoteki klienta. Wybory firm w programie pokazują
nazwy skrócone, dokumenty otrzymują pełną nazwę prawną.

**Generuj komplet PDF…** zapisuje jeden plik w kolejności: umowa ramowa,
OWU, cennik, powierzenie, lista kontrolna, PEP, uzgodnienie KSeF, klauzula
RODO, upoważnienie. Wypowiedzenie można dołączyć po zaznaczeniu. Poszczególne
dokumenty można odznaczyć. Opcja dwustronna dodaje pustą stronę między
dokumentami, jeśli jest potrzebna, aby kolejny dokument zaczynał się na
nowej kartce. Nie wysyła pliku automatycznie do drukarki.

PDF wymaga zainstalowanego Microsoft Word na Windows lub LibreOffice.
Generator zachowuje źródłowy układ Worda, nie przepisuje dokumentów do HTML.
Konwersja działa w tle. Word uruchamiany jest z wyłączonymi makrami i
aktualizacją linków. Nie zmienia globalnych ustawień polityki PowerShell;
polityka organizacji może zablokować automatyzację. Wtedy można użyć
LibreOffice lub zapisać **Komplet DOCX (ZIP)** bez konwertera PDF.
Różne programy i brakujące czcionki mogą wpływać na podział stron.
Niezmieniane OWU są wstawiane z dostarczonego przez użytkownika PDF, dzięki
czemu zachowują jego oryginalny dziewięciostronicowy układ. DOCX OWU jest
dostępny w edytowalnym komplecie ZIP.

Podpisy, zgody i oświadczenia klienta, w tym status PEP, pozostają puste do
jego wypełnienia. Generator nie uznaje udziałowca automatycznie za osobę
uprawnioną do reprezentacji. Dane udziałowców z kartoteki są używane w
części listy kontrolnej dotyczącej wspólników. Pole podstawowego
wynagrodzenia w cenniku uzupełnia się przed generowaniem; pozostałe stawki
pochodzą z przesłanego wzoru i nie są zastępowane cennikami aplikacji.

Wzory w `app/documents/templates/` pochodzą z plików przekazanych przez
użytkownika 6 listopada 2026. Zachowano ich treść poza polami przeznaczonymi
do uzupełnienia. W szczególności cennik jest oznaczony jako obowiązujący od
stycznia 2026, OWU zawierają zapis o rozliczeniu grudnia 2025, a wzór
wypowiedzenia zawiera adres biura z kodem 96-515. Te postanowienia/dane
źródłowe wymagają weryfikacji przez właściciela wzorów przed użyciem;
generator nie poprawia ich samodzielnie. Daty zawarcia powierzenia i umowy
głównej są zastępowane datami wybranymi w formularzu.

## UPL-1 i PEL

Można dołączyć sprawdzone PDF-y przyciskiem **Dodaj PDF-y, np. UPL-1 / PEL…**.
Są dopisywane po dokumentach Word w kolejności wyboru. Ta funkcja nie
wypełnia automatycznie ich pól i nie nadaje pełnomocnictw w urzędzie/ZUS.

Oficjalne źródła sprawdzone 6 listopada 2026:

- [MF: formularze pełnomocnictw, UPL-1(6)](https://podatki.gov.pl/pozostale/pelnomocnictwa/formularze-pelnomocnictwa)
- [UPL-1(6) PDF](https://podatki.gov.pl/media/nx3lnwfo/upl-1_06-08-2.pdf)
- [ZUS: PEL, wersja interaktywna i do druku](https://www.zus.pl/wzory-formularzy/pelnomocnictwo/pel-pelnomocnictwo)

UPL-1/PEL nie były częścią przesłanych załączników. Polityka sieciowa
środowiska uniemożliwiła pobranie ich plików do implementacji; nie dodano
niezweryfikowanego mapowania pól ani imitacji urzędowych formularzy.

## Wdrożenie i sprawdzenie

Zainstaluj aktualne zależności (`requirements.lock` / `requirements.txt`) i
przebuduj EXE oraz instalator. Specyfikacja PyInstaller obejmuje wzory.
Zmiana nie wymaga migracji bazy danych.

Testy obejmują uzupełnienie rzeczywistych wzorów, zachowanie tabel i grafiki,
stany checkboxów Worda, osobne daty umów, kolejność plików, strony separujące,
autoryzację, brak powtórnego eksportu podczas generowania i zachowanie
istniejącego pliku przy błędzie. Wszystkie dziesięć DOCX przekonwertowano
rzeczywiście w LibreOffice w izolowanym kontenerze. Ścieżka automatyzacji
Microsoft Word wymaga jeszcze sprawdzenia na Windows z zainstalowanym Wordem.
