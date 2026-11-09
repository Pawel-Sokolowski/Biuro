# Automatyczne przypomnienia e-mail

W **Ustawienia → Poczta i powiadomienia → Automatyczne e-maile** administrator
może włączyć osobno przypomnienia o dokumentach i płatnościach. Domyślnie obie
reguły są wyłączone. Najpierw skonfiguruj konto nadawcy w **Konto systemowe i kalendarz**.

- Dokumenty: wybierz dzień następnego miesiąca będący terminem dostarczenia
  dokumentów za poprzedni miesiąc (np. 10 listopada za październik). Domyślne
  pierwsze przypomnienie jest dzień po tym terminie, potem co 3 dni. Sprawdzane
  są wyłącznie aktywne firmy z przypisaną księgową, również bez utworzonego
  wiersza statusu miesięcznego. Firmy obsługiwane wyłącznie kadrowo lub bez
  przypisanej księgowej są pomijane. Obsługa kadr nie jest warunkiem przypomnienia.
  Zaznaczenie „Dokumenty dostarczone” zatrzymuje wysyłkę. Dokumenty dotyczą
  poprzedniego miesiąca; po rozpoczęciu kolejnego miesiąca reguła przechodzi
  do kolejnego okresu, bez wysyłania przypomnień o starszych okresach.
- Płatności: termin pochodzi z faktury. Domyślne dni to `0, 2, 3` (w terminie,
  dwa i trzy dni po nim), potem co 3 dni. Saldo uwzględnia zarejestrowane
  płatności i kompensaty. Sprawdzane są wystawione faktury zwykłe i zaliczkowe;
  robocze, proformy, korekty, anulowane i kosz są pomijane. Pełna zapłata zatrzymuje wysyłkę.
- Każda reguła ma własne dni tygodnia, godzinę w strefie `Europe/Warsaw`, wybór
  wysyłania w polskie święta ustawowe i szablon. Niedozwolone dni przesuwają
  termin na najbliższy dozwolony dzień. Zbieżne terminy dają jedną wiadomość.
  `0` w polu powtarzania wyłącza dalsze cykliczne przypomnienia.
- Szablony można dodawać, edytować i wybierać osobno dla obu reguł. Pola:
  `{firma}`, `{miesiac}`, `{termin}`, `{numer}`, `{pozostalo}`. Podgląd używa
  danych przykładowych i niczego nie wysyła. Numer i saldo dotyczą płatności.
- Dokumenty są wysyłane na adres firmy. Dla faktur pierwszeństwo ma adres
  z faktury, następnie adres do faktur firmy i zwykły adres firmy lub kontrahenta.
  Kilka adresów można oddzielić przecinkiem albo średnikiem (maks. 10).

Aplikacja sprawdza terminy co minutę, gdy jest uruchomiona. Po przerwie wysyła
jedno najnowsze zaległe przypomnienie, bez serii wiadomości za wszystkie pominięte
terminy. Włączenie reguły płatności obejmuje również już zaległe faktury.
Wspólna historia w PostgreSQL zapobiega powielaniu wysyłki z kilku stanowisk.

## Wysyłka przy zamkniętej aplikacji

Na komputerze lub serwerze z dostępem do tej samej bazy, ustawieniami połączenia
`DB_URL` i zależnościami aplikacji uruchamiaj co minutę z katalogu projektu:

```sh
.venv/bin/python -m app.services.email_reminders
```

Na Windows użyj `.venv\Scripts\python.exe -m app.services.email_reminders`
w Harmonogramie zadań. Ustaw katalog roboczy na katalog projektu. Użyj tego
samego środowiska i klucza szyfrowania haseł co aplikacja. Przed pierwszym
uruchomieniem zaktualizuj bazę zwykłym startem aplikacji lub `python migrate_db.py`.
Sam moduł nie instaluje harmonogramu systemowego.

Historia w ustawieniach pokazuje 100 ostatnich prób. `sent` oznacza potwierdzoną
wysyłkę SMTP, `failed` błąd, a `sending` próbę rozpoczętą (może wymagać sprawdzenia
po przerwaniu procesu). Nie ma automatycznego ponawiania tego samego terminu po
niejednoznacznym błędzie SMTP, aby uniknąć duplikatu. Kolejny zaplanowany termin
nadal obowiązuje. Brak adresu lub niepoprawny adres jest pomijany i odnotowany
w logu aplikacji. Samo zapisanie ustawień ani testy nie wysyłają wiadomości.
