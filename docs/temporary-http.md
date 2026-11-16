# Tymczasowe połączenie bez certyfikatu

W konfiguracji workera otwórz **Usługa biura**, włącz udostępnianie usługi
oraz **Tymczasowo używaj HTTP bez certyfikatu**. Zapisz ustawienia i uruchom
ponownie worker. W logowaniu programu wpisz pełny adres
`http://ADRES-SERWERA:8443` (lub ustawiony port). Certyfikat ani klucz TLS
nie są wymagane w tym trybie. Odpowiada mu lokalne ustawienie
`BIO_API_ALLOW_HTTP=1` w konfiguracji workera.

HTTP przesyła hasła, sesje i dane bez szyfrowania połączenia. Logowanie,
uprawnienia oraz szyfrowanie przechowywanych sekretów pozostają aktywne;
klucz szyfrowania nadal pozostaje na serwerze. Nie wystawiaj tego trybu
bezpośrednio do internetu.

Dotychczasowe instalacje zachowują HTTPS. Aby wrócić do niego, wyłącz opcję,
ustaw certyfikat i klucz TLS, zapisz, zrestartuj worker i zmień adres klienta
na `https://`. Program nie przełącza się automatycznie z HTTPS na HTTP
ani nie pomija weryfikacji certyfikatów HTTPS. Adres bez protokołu nadal
jest interpretowany jako HTTPS.

Zmiana dotyczy połączenia programu Biuro z workerem. Dodatek Enova nadal
używa HTTPS. Zmiana nie rozwiązuje niezgodności Qt z Windows Server 2016.
