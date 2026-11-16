# Mailing o niewykorzystanych wakacjach ZUS

W oknie mailingu zaznacz **Wakacje ZUS — niewykorzystane**, wybierz rok i miesiąc ulgi.
**Warunki potwierdzone** wybiera osoby, dla których biuro potwierdziło wszystkie
warunki. **Do weryfikacji** pokazuje brakujące dane i pozwala przygotować osobny
mailing informacyjny. Wiersz wyjaśnia wynik; przycisk **Sprawdź / uzupełnij warunki**
otwiera formularz dla właściciela lub poszczególnych wspólników. Można go otworzyć
także ze zwykłej listy firm, żeby poprawić wcześniej zapisane odpowiedzi.

Program korzysta z zaimportowanych wpisów RWS i kodu z zestawienia Wakacje ZUS.
Kod z poprzedniego roku może zostać podpowiedziany w formularzu; trzeba sprawdzić
jego aktualność. Brak RWS nie dowodzi niewykorzystania ulgi. Płatnik nie potwierdza
samodzielnie przychodów, współpracy z byłym pracodawcą ani dostępnej pomocy de minimis:
te fakty wymagają weryfikacji biura. Nieznane odpowiedzi nie kwalifikują osoby do
grupy z potwierdzonymi warunkami.

## Stosowane zasady

- Zwykłe tytuły działalności: 05 10, 05 12, 05 70, 05 72, 05 90, 05 92.
  Kody ulgi 05 14, 05 74 i 05 94 nie zastępują kodu bazowego. Kod sam w sobie
  nie przesądza o prawie do wakacji. Ulga na start i osoby współpracujące nie
  spełniają tego warunku.
- Uprawniony jest przedsiębiorca CEIDG lub komornik, w tym wspólnik SC indywidualnie.
  Spółka wpisana do KRS nie korzysta z tej ulgi jako osoba prawna.
- Ulga obejmuje jeden miesiąc w roku. Wniosek składa się w miesiącu bezpośrednio
  poprzedzającym miesiąc ulgi. Po upływie tego terminu osoba nie jest wybierana.
- W miesiącu poprzedzającym miesiąc wniosku: 1–10 unikalnych ubezpieczonych
  łącznie z przedsiębiorcą oraz podleganie E/R/W choć przez jeden dzień.
  Nie jest to liczba pracowników na koniec miesiąca. Uwzględnij wyjątki opisane
  przez ZUS dla osób przez cały miesiąc na urlopach macierzyńskich, wychowawczych
  lub bezpłatnych. Dla wspólnika SC liczy się jego indywidualne konto, bez
  pracowników zgłoszonych przez spółkę.
- W dwóch latach przed rokiem wniosku: brak przychodów albo przychód do równowartości
  2 mln EUR w co najmniej jednym roku. Przykład: ulga za styczeń 2027, wniosek
  w grudniu 2026, liczba ubezpieczonych za listopad 2026, przychody za 2024 i 2025.
- Spełniony warunek niewykonywania zakazanych czynności dla byłego pracodawcy
  w poprzednim roku i roku wniosku do dnia jego złożenia.
- Pozostały limit pomocy de minimis musi wystarczyć na zwolnienie ze składek.
  Samo nieprzekroczenie ogólnego limitu nie wystarcza.

Aktywny wpis RWS (`A`) w roku ulgi blokuje mailing o niewykorzystaniu, nawet przy
ręcznym zaznaczeniu „niewykorzystane”. To ostrożne wykluczenie: wpis RWS nie jest
samodzielnym dowodem pozytywnej decyzji ZUS. `N` oznacza wpis nieaktywny, nie
odmowę. Nieznany status albo zmiana wpisów od potwierdzenia wymagają weryfikacji.
Warunki zapisane z wyprzedzeniem trzeba ponownie potwierdzić w miesiącu wniosku.

W SC każdy wspólnik ma osobny wynik według indywidualnego NIP-u. Mailing trafia
wyłącznie na adresy wspólników wybranej grupy. Nie tworzy dodatkowych firm JDG.
Przed każdą partią serwer ponownie sprawdza warunki, wpisy RWS, dostęp do firmy
i adresy. Zmiana zatrzymuje wysyłkę do ponownego przejrzenia listy.

Warunki są szyfrowane w bazie serwera, z identyfikatorem pracownika, datą i wersją
zapisu; równoczesna edycja nie nadpisuje cudzych zmian. Klient nie pobiera klucza.
Miesiące w zestawieniu Wakacje ZUS są wyświetlane nazwami; import zachowuje numery.

## Źródła

- [ZUS — zasady wakacji składkowych](https://www.zus.pl/-/zwolnienie-z-op%C5%82acania-sk%C5%82adek-zus-wakacje-sk%C5%82adkowe)
- [ZUS — pytania i odpowiedzi](https://www.zus.pl/baza-wiedzy/biezace-wyjasnienia-komorek-merytorycznych/wakacje-skladkowe-pytania-i-odpowiedzi)
- [Oficjalny wykaz kodów ubezpieczenia, Dz.U. 2026 poz. 518](https://eli.gov.pl/api/acts/DU/2026/518/text/I/D20260518.pdf)

Stan zasad sprawdzony 16 listopada 2026. Zmiany prawa wymagają aktualizacji reguł.
