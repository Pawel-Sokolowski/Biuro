# Komplety dokumentów klienta

W zakładce **Klienci** zaznacz firmę i wybierz **Komplet dokumentów…**.
Sprawdź pełną nazwę, adres, identyfikatory, daty, reprezentantów, zakres
obsługi i parametry usługi. Zmiany w tym oknie dotyczą wyłącznie generowanego
kompletu, nie zmieniają kartoteki klienta. Wybory firm w programie pokazują
nazwy skrócone, dokumenty otrzymują pełną nazwę prawną.
Daty wybiera się z kalendarza z nawigacją miesięcy. Przedstawiciela można
wybrać spośród osób kontaktowych i wspólników zapisanych przy firmie lub
wpisać nową osobę. Funkcja (np. Prezes zarządu lub Członek zarządu) jest
edytowalna i drukuje się przy wskazanym reprezentancie w obu umowach.
PESEL przedstawiciela jest opcjonalny.

**Generuj komplet PDF…** zapisuje jeden plik w kolejności: umowa ramowa,
OWU, cennik, powierzenie, lista kontrolna, PEP, uzgodnienie KSeF, klauzula
RODO, upoważnienie. Wypowiedzenie można dołączyć po zaznaczeniu. Poszczególne
dokumenty można odznaczyć. Opcja dwustronna dodaje pustą stronę między
dokumentami, jeśli jest potrzebna, aby kolejny dokument zaczynał się na
nowej kartce. Nie wysyła pliku automatycznie do drukarki.

PDF-y dokumentów Word wymagają zainstalowanego Microsoft Word na Windows lub LibreOffice.
UPL, PEL i PEL-Z aplikacja tworzy samodzielnie, bez zewnętrznego konwertera.
Generator zachowuje źródłowy układ Worda, nie przepisuje dokumentów do HTML.
Konwersja działa w tle. Word uruchamiany jest z wyłączonymi makrami i
aktualizacją linków. Nie zmienia globalnych ustawień polityki PowerShell;
polityka organizacji może zablokować automatyzację. Wtedy można użyć
LibreOffice lub zapisać **Komplet DOCX + PDF (ZIP)** bez konwertera PDF.
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

## UPL-1, PEL i PEL-Z

W zakładce **UPL / PEL** wybierz pracownika jako pełnomocnika. Imię i nazwisko
pochodzą z jego konta; nie można podstawić innej osoby przez edycję danych
wydruku. Pracownik jest ponownie sprawdzany przy generowaniu i musi być aktywny.
Jego adres do korespondencji pochodzi z danych biura w **Ustawienia → Faktury
i KSeF**, a e-mail pochodzi z konfiguracji sprzedawcy (lub stopki),
nie z prywatnego e-maila pracownika. W repozytorium nie ma domyślnego adresu biura. Można tam
uzupełnić również województwo, powiat i gminę biura. Prywatnego adresu
pracownika nie używa się w formularzach.

Uzupełnij identyfikator pełnomocnika (PESEL, NIP w UPL lub dokument tożsamości),
rodzaj mocodawcy i daty. PEL zawsze obejmuje sprawy w ZUS i pełny dostęp
do odpowiednich ról eZUS, zgodnie z opisem poniżej.
Pełnomocnictwa są zawsze bezterminowe / do odwołania: data „ważne do”
pozostaje pusta, także gdy starszy formularz przekazuje datę końcową.
Pole „Adres elektroniczny” w UPL pozostaje puste; nie jest to pole kontaktowego
e-maila, który jest pobierany z konfiguracji biura.
Przesłany XSD UPL wymaga daty urodzenia pełnomocnika; można ją wyliczyć z PESEL
albo wpisać, gdy używany jest inny identyfikator. Przy zmianie pracownika
poprzednie identyfikatory są czyszczone. Dane identyfikacyjne wpisane w oknie
nie są zapisywane do kartoteki pracownika. PESEL przedstawiciela klienta nadal
pozostaje opcjonalny. Adres klienta ma jeden zestaw pól wspólny dla umów i
pełnomocnictw.

W zakładce **Dokumenty** zaznacz potrzebne pełnomocnictwa:

- **UPL-1(6)** zachowuje oryginalny dwustronicowy PDF `upl-1_06-04.pdf`
  dostarczony przez użytkownika. Aplikacja wpisuje dane w sprawdzonym układzie;
  użytkownik nie tworzy ani nie rozmieszcza pól. Podpisy i uwierzytelnienie
  pozostają puste. Imiona, nazwiska i funkcje przedstawicieli są drukowane
  osobno z danych sprawdzonych w oknie. Przesłany starszy XSD wymaga daty
  uwierzytelnienia w całym bloku podpisów; nie jest ona wymyślana, a ten blok
  jest pomijany w wewnętrznym niepodpisanym XML.
- **PEL** używa przesłanego trzystronicowego PDF-u `PEL_pobierz_druk.pdf`
  (druk 579/26). Dla JDG wypełnia dane właściciela i zaznacza działanie w imieniu
  „mojej firmy”. Dla osoby prawnej lub pozostałej spółki wpisuje pierwszego
  wybranego przedstawiciela, zaznacza „innego podmiotu” i dodaje nazwę, adres
  oraz REGON tego podmiotu. Przy wielu przedstawicielach trzeba wybrać PEL-Z;
  zaznaczenie załączenia wynika z rzeczywiście wybranych dokumentów.
  Zawsze zaznacza załatwianie spraw w ZUS i przez eZUS, bez ograniczeń.
  Dla JDG/osoby fizycznej zaznacza role Ubezpieczony i Płatnik składek;
  dla osoby prawnej i pozostałych spółek tylko Płatnik składek w sekcji
  reprezentowanego podmiotu. Pola ograniczeń korespondencji, E-ZLA i typów
  wniosków pozostają puste. Stanowisko/funkcję przedstawiciela wybiera się
  z listy lub wpisuje ręcznie. PIT wymaga osobnego zaznaczenia.
  Ten PEL nie ma pola e-mail.
  Stary wzór XML/XSL CRD pozostaje zasobem do walidacji, ale nie jest już używany
  do generowania PEL w komplecie do druku.
- **PEL-Z** używa przesłanego trzystronicowego PDF-u. Aplikacja odczytuje
  etykiety i wektorowe granice pól tej wersji, wpisując identyfikatory, datę
  wypełnienia, dane przedstawicieli i ich adresy. Podpisy pozostają puste.
  W danych reprezentanta można uzupełnić adres, datę urodzenia i dokument
  tożsamości do PEL-Z. Szablon obsługuje do czterech przedstawicieli; obecne
  okno umów udostępnia dwóch zgodnie z podstawowymi szablonami umów.

Wzory PDF UPL, PEL i PEL-Z są sprawdzane sumą SHA-256. Nie zastosuje się położenia
pól do innego pliku lub nieznanej wersji formularza. Tekst jest wektorowy,
czcionka danych ma 12 pt, z dopasowaniem do szerokości ograniczonych pól.
E-mail zachowuje dokładną pisownię. Formularze są dopisywane do jednego PDF-u
po wybranych dokumentach Word; eksport ZIP zawiera DOCX oraz gotowe PDF-y
pełnomocnictw. Zapis nie wysyła dokumentów, nie podpisuje ich elektronicznie
ani nie nadaje uprawnień w urzędzie/ZUS.

Rodzaj mocodawcy wynika z zapisanej formy działalności, z możliwością ręcznej
zmiany: JDG i osoby fizyczne → osoba fizyczna; spółki cywilne, jawne,
komandytowe i partnerskie → inny podmiot; spółki z o.o. i akcyjne → osoba
prawna. Sam numer KRS nie ustawia rodzaju. Nieznana forma wymaga wyboru.
W UPL tylko JDG/osoba fizyczna zaznacza „osoba fizyczna”; oba pozostałe
rodzaje zaznaczają „podmiot niebędący osobą fizyczną”. Funkcję przedstawiciela
w UPL wpisuje się dla podmiotów niebędących osobą fizyczną.

Wszystkie zależności XSD, w tym `CountryCode.xsd` i schemat podpisu XML, są
zapisane lokalnie w `app/documents/templates/crd/` z katalogiem źródłowych
URL-i i sumami kontrolnymi. Schematy UPL `2018/11/02/6046` i PEL
`2012/11/29/1053` kompilują się w pełni offline. Nie podstawia się uproszczonych
schematów ani nie pomija walidacji przy brakujących zasobach. Z wklejonego
schematu podpisu usunięto wyłącznie komunikat przeglądarki, zachowując jego
zawartość i notę licencyjną. Wewnętrzny XML nie jest eksportowany jako dokument
przeznaczony do elektronicznego składania.

Można też dołączać własne gotowe PDF-y. Ta opcja nie wypełnia automatycznie
nieznanych formularzy. Oficjalne źródła wersji formularzy:

- [MF: pełnomocnictwa UPL-1](https://podatki.gov.pl/pozostale/pelnomocnictwa/formularze-pelnomocnictwa)
- [ZUS: PEL](https://www.zus.pl/wzory-formularzy/pelnomocnictwo/pel-pelnomocnictwo)

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


## Wielu pełnomocników i dane lokalne

W Ustawienia → Użytkownicy → Dane do PEL / UPL zapisz PESEL/NIP, datę urodzenia
lub dane dokumentu pracownika. Dane identyfikacyjne są szyfrowane we wspólnej
bazie biura i nie trafiają do repozytorium. Nazwisko i aktywność pracownika są
ponownie sprawdzane przy generowaniu. W komplecie dokumentów zaznacz pracowników
checkboxami: każdy otrzyma osobny UPL/PEL, w jednym PDF albo ZIP. Poprawki
w dialogu dotyczą konkretnej osoby i danego wydruku.

Dane biura i reprezentanta umowy ustaw w Faktury i KSeF; stopkę ustaw osobno.
Szablony mają neutralne pola, bez logo, danych biura i metadanych autora.
Warunki umowy są teraz renderowane z DOCX z aktualną konfiguracją, zamiast
z wcześniej wypełnionego, stałego PDF. Wymaga to Word/LibreOffice jak inne umowy.

Wspólnicy spółki cywilnej mają osobne NIP, PESEL, adresy i e-maile w kartotece
firmy. W pełnomocnictwach wybór mocodawcy „Firma” zachowuje NIP spółki; wybór
konkretnego wspólnika używa jego indywidualnych danych. Dane wspólników wypełniają
przedstawicieli spółki; każdą osobę i zakres trzeba sprawdzić przed podpisem.
Powiązanie wspólnika nie tworzy ani nie scala kartotek JDG i spółki.

Worker e-ZLA odczytuje NIP spółki i zapisane NIP-y jej wspólników. Nowy NIP
ma własny zakres początkowy i kursor; błąd jednego płatnika nie kasuje raportów
pobranych dla pozostałych. Raport nadal zachowuje rzeczywisty NIP płatnika.
Ten sam właściciel może być jawnie powiązany z kilkoma firmami; raport może być
wtedy widoczny w tych firmach, bez zgadywania relacji po nazwisku.

Domyślni adresaci poczty do spółki cywilnej to wszyscy zapisani wspólnicy:
faktury, przypomnienia i lista firm do poczty masowej. Nie obejmuje to wszystkich
kontaktów z książki adresowej. Brak adresu któregoś wspólnika blokuje domyślną
wysyłkę zamiast po cichu wysyłać tylko do jednej osoby. Odbiorcy pozostają
widoczni do sprawdzenia przed ręczną wysyłką.
