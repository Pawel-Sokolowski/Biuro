# Historyczne liczby dokumentów księgowości z XLSX

W **Raporty** wybierz **Importuj liczbę dokumentów (Excel)** i wskaż roczny
plik z liczbami dokumentów księgowości. Import jest także ostatnim, opcjonalnym
krokiem w **Ustawienia → Import danych**. Może go wykonać administrator lub
użytkownik z uprawnieniem do finansów.

Nie trzeba dostosowywać nazwy pliku. Wybierasz plik dokumentów, a nie podobny
plik kadr. **Kadry nadal są odtwarzane z pozycji faktur z podziałem UoP/UZ/UoD**;
łączna liczba z rocznego arkusza kadrowego nie jest przypisywana do jednego typu umów.

Obsługiwany układ:

| Firma | NIP | Razem | styczeń 2026r. | luty 2026r. | marzec 2026r. |
| --- | --- | --- | --- | --- | --- |
| Nazwa dowolna | 6741006281 | 34 | 19 | 15 | |

- Firma jest dopasowywana **wyłącznie po jednoznacznym NIP**. Nazwa może być
  inna niż w aplikacji. NIP może być tekstem, liczbą Excela, zawierać spacje,
  myślniki lub prefiks PL. Nieprawidłowy NIP (np. 233), brak firmy lub kilka
  firm o tym samym NIP są raportowane. Nie tworzy się nowych firm i nie szuka
  się NIP w nazwie firmy ani w sąsiednich kolumnach.
- „Razem” jest ignorowane. Liczby pochodzą bezpośrednio z poszczególnych
  miesięcy, bez przeliczania faktur, cen lub limitów dokumentów.
- Pusty miesiąc i kreska pozostawiają istniejący stan. **Wpisane 0 jest zerem**.
  Formuły bez zapisanego wyniku są pomijane z ostrzeżeniem — można otworzyć
  plik w Excelu, przeliczyć i zapisać przed ponownym importem.
- Kolejność kolumn, inne nazwy plików, wiele arkuszy i dodatkowe wiersze nad
  nagłówkiem są obsługiwane. Rozpoznawane są polskie nazwy miesięcy z rokiem,
  `YYYY-MM` oraz nagłówki zapisane jako daty Excela. Miesiąc zawsze pochodzi
  z nagłówka, nie z bieżącej daty ani nazwy pliku.
- Powtórzone identyczne wartości tego samego NIP/miesiąca nie są sumowane.
  Sprzeczne wartości, ułamki i liczby ujemne przerywają import przed zapisem.

Domyślnie istniejące różne, niezerowe liczby są zachowane i uwzględnione w
podsumowaniu. Aby zastąpić wcześniejsze oszacowania, zaznacz **Nadpisz istniejące
dane (ilość dokumentów w widoku miesięcznym)** przed importem. W kreatorze
ustawień pojawia się analogiczny wybór. Ponowne wczytanie tych samych danych
nie dodaje ich do poprzedniej wartości.

Bezpośrednio importowane liczby są źródłem dla widoku miesięcznego, raportu
rocznego/miesięcznego, dziennego i rentowności — również gdy aplikacja zawiera
pojedyncze fizyczne dokumenty. Zwykły import faktur nadal uzupełnia kadry,
lecz nie nadpisuje tych księgowych liczb szacunkiem z ceny faktury. Ręczna
zmiana liczby w widoku miesięcznym jest od razu widoczna w raportach.
Pozostałe pola (czas, etapy, komplet dokumentów, kadry) są zachowane.

Po imporcie odśwież widok miesięczny lub wygeneruj raport ponownie.
Podsumowanie pokazuje zmienione i zachowane miesiące oraz uwagi. Historia
jest dostępna w ustawieniach audytu/importów jako `monthly_document_counts_xlsx`.
Przy aktualizacji aplikacji migracja tworzy tabelę pochodzenia danych
`monthly_document_imports`; nie kopiuje danych klientów z arkuszy do repozytorium.
