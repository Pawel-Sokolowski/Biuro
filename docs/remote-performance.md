# Przycięcia po połączeniu z serwerem

Istniejący `service_scope` uruchamia cache na czas operacji. Cache nie jest
współdzielony między operacjami ani użytkownikami; nie zmieniamy jego zakresu.
Połączenia TCP serwera używają TCP_NODELAY, aby małe odpowiedzi HTTP/TLS nie
czekały na buforowanie Nagle'a przy osobnym wysyłaniu nagłówków i treści.
Weryfikacja certyfikatu i sesji pozostaje aktywna.

Wywołania trwające co najmniej 250 ms zapisują w standardowym `biuro.log`:

- klient: `office_request_slow`, trasę, nazwę operacji, czas i `main_thread`;
- serwer: `office_operation_slow`, nazwę operacji i czas wykonania.

Logi są w katalogu danych Biura, zwykle `%LOCALAPPDATA%\Biuro\logs`.
Wpisy nie zawierają argumentów, tokenów, adresów serwera ani danych firm.
`main_thread=True` wskazuje oczekiwanie w wątku interfejsu. Duży czas po
stronie klienta przy krótkiej operacji serwera sugeruje komunikację lub
kolejkę serwera; długi czas na serwerze wymaga sprawdzenia konkretnej
operacji i bazy. Brak wolnych wpisów nie wyklucza wielu krótkich wywołań
wykonywanych kolejno ani kosztu renderowania interfejsu.

Te zmiany usuwają rozpoznane źródła narzutu. Bez logów z instalacji nie
potwierdzają głównej przyczyny zgłoszonych przycięć ani skali poprawy.

`office_navigation_slow` opisuje całe przełączenie zakładki: indeks widoku,
czas, liczbę żądań sieciowych wykonanych synchronicznie, ich łączny czas i pięć
najdroższych nazw operacji. Uwzględnia także żądania krótsze niż 250 ms,
które mogą sumować się do długiego oczekiwania. Pomiar nie obejmuje odczytów
w tle rozpoczętych przez widok. Dużo czasu sieciowego oznacza oczekiwanie
na API; długi czas przejścia przy małym czasie sieciowym wskazuje na lokalne
budowanie/renderowanie widoku. Potrzebne są logi z komputera pracownika i
serwera z tej samej próby przełączenia.
