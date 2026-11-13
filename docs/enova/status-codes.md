# Kody statusów Enova użyte przez adaptery

Źródło: publiczne pakiety producenta
[Soneta.Core](https://www.nuget.org/packages/Soneta.Core/2606.0.1) i
[Soneta.Deklaracje](https://www.nuget.org/packages/Soneta.Deklaracje/2606.0.1).
Odczytano statyczne metadane typów i stałych enumów z dostarczonych DLL,
bez wykonywania bibliotek. Porównano wydania **2506.0.0**, **2606.0.1** i **2610.0.2**:
poniższe wartości są identyczne. Pakiety/biblioteki oraz prywatne eksporty baz nie
są dołączone do repozytorium ani instalatora Biuro.

| Typ | Wartość | Nazwa producenta | Stan w Biuro |
| --- | --- | --- | --- |
| StatusPlikuJPK | 10 | Created | przygotowany |
| StatusPlikuJPK | 15 | WaitingForSignature | przygotowany |
| StatusPlikuJPK | 20 | ReadyToSend | niewysłany |
| StatusPlikuJPK | 30 | SendingFiles | przetwarzany |
| StatusPlikuJPK | 40 | ProcessingByGate | przetwarzany |
| StatusPlikuJPK | 100 | Finished | przyjęty tylko z UPO na oficjalnej bramce |
| StatusPlikuJPK | 200 | Failed | błąd przetwarzania |
| StatusEDeklaracji | 0 | Brak | nieustalony |
| StatusEDeklaracji | 1 | DoPodpisania | przygotowany |
| StatusEDeklaracji | 2 | DoWysłania | niewysłany |
| StatusEDeklaracji | 3 | DoPotwierdzeniaOdbioru | wysłany, oczekuje na UPO |
| StatusEDeklaracji | 4 | Potwierdzona | przyjęty tylko z referencją UPO |
| StatusEDeklaracji | 99 | Błędna | błąd przetwarzania |
| StatusEDeklaracji | 100 | Anulowana | anulowany |
| StatusEDeklaracji | 200 | DodanaDoZbiorczej | nieustalony, wymaga odczytu zbiorczego |
| BramkaJPKFile | 1 | Nieokreslona | nie potwierdza złożenia produkcyjnego |
| BramkaJPKFile | 2 | Testowa | nie potwierdza złożenia produkcyjnego |
| BramkaJPKFile | 3 | Oficjalna | bramka produkcyjna |
| TypETransmisji | 0 | Brak | pomijany przy odczycie odpowiedzi UPO |
| TypETransmisji | 1 | WysyłanieDokumentu | próba wysłania, nie odpowiedź UPO |
| TypETransmisji | 2 | PotwierdzenieOdbioru | odpowiedź sprawdzania statusu/UPO |

`StatusETransmisji` jest kodem odpowiedzi MF, a nie powyższym enumem.
200 oznacza poprawne przetworzenie, 301–304 przetwarzanie, grupa 400 odrzucenie;
[opis Ministerstwa Finansów](https://podatki.gov.pl/e-deklaracje-zloz-deklaracje-podatkowa-online/informacje-podstawowe).
Adapter deklaracji bierze odpowiedź z najnowszej transmisji **Typ=2**. Anulowanie
ma pierwszeństwo przed starym potwierdzeniem. Daty próby wysyłania nie są
przedstawiane jako potwierdzone daty złożenia.

JPK wybiera najnowszą korektę na oficjalnej bramce. Nowszy plik testowy nie
zasłania produkcyjnego złożenia; gdy istnieje tylko testowy, stan jest nieustalony,
a pole UPO pozostaje puste. Nieznane wartości w przyszłych wydaniach także
pozostają nieustalone. Zawsze zachowujemy surowy kod do diagnostyki.

XML odczytujemy z kolumn Unicode `ntext`; deklarację kodowania XML usuwamy przed
konwersją na typ SQL `xml`, ponieważ zawartość jest już tekstem Unicode, a
nagłówek może deklarować UTF-8. Niepoprawne XML nie daje potwierdzenia UPO.
Data potwierdzenia jest zwracana jako ISO 8601, co omija nieobsługiwany przez
standardowy odczyt pyodbc typ SQL `datetimeoffset` i zachowuje strefę czasową.

Workflow **Import SQL Server** uruchamia dołączone zapytania przez ODBC na
SQL Server 2022 z syntetycznymi danymi i użytkownikiem posiadającym wyłącznie
SELECT. Obejmuje korekty, nazwy formularzy w XML, kody, anulowanie, bramkę
testową, błędny XML, miesiące bez danych i oddzielne dokumenty płacowe.
Nie korzysta z baz klientów, eksportów JSON ani danych logowania użytkownika.
