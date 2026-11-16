# Przycięcia po połączeniu z serwerem

Odczyty wykonywane przez API otrzymują osobny `request_scope`: powtarzające się
odczyty ustawień i uprawnień mogą korzystać z istniejącego cache'u wewnątrz
jednej operacji. Cache nie przechodzi między operacjami ani użytkownikami.
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
