# Uprawnienia do faktur

Dostęp **Faktury** w macierzy modułów oznacza pełną zwykłą obsługę faktur biura:
podgląd, tworzenie i edycję roboczych, wystawianie, korekty, PDF i wysyłkę KSeF.
Obejmuje też faktury ręczne dla kontrahentów i osób fizycznych oraz dokumenty
z zachowaną tożsamością nabywcy bez powiązania z aktywną kartoteką firmy.
Nie wymaga równoczesnego zaznaczania Finansów i Eksportu danych.

Administrator ustawia to w **Ustawienia → Role i uprawnienia**. Pole „Faktury”
w edycji roli i kolumna „Faktury — pełna obsługa” w macierzy zmieniają to samo
uprawnienie i są zapisywane spójnie. Zapis macierzy synchronizuje starszą flagę
`can_view_invoices`, a zmiana tej flagi przez edycję roli aktualizuje macierz.

Macierz modułów jest rozstrzygająca. Jeżeli „Faktury” jest w niej odznaczone,
stara flaga `can_view_invoices`, `can_view_finances` albo `can_export_data` nie
przywraca dostępu. Stare konfiguracje bez zapisanej macierzy zachowują dotychczasowy
domyślny zestaw zakładek; nie wykonujemy masowego nadpisania istniejących ról.
Nowo tworzone role otrzymują spójny, jawny zestaw modułów według wybranego dostępu.
Administrator nadal ma pełny dostęp. Nieaktywny lub nieznany użytkownik nie ma
uprawnień do operacji na fakturach.

Dodatkowe uprawnienia mają osobny zakres:

| Ustawienie | Zakres |
| --- | --- |
| Faktury | Zwykła obsługa dokumentów, w tym wystawianie i korekty |
| Płatności / finanse | Dodatkowo rozliczanie wpłat, uzgadnianie banku i raporty finansowe |
| Eksport zbiorczy | Zbiorcze pliki danych, np. XLSX/Saldeo i JPK; nie jest wymagany do wystawienia ani wysyłki KSeF/PDF |
| Edycja firm | Zmiany kartoteki firm |
| Zarządzanie użytkownikami | Administracja użytkownikami i rolami w dostępnym zakresie ustawień |

Bez dodatku finansowego zakładka Rozliczenia jest ukryta, a przyciski banku/wpłat
nie są aktywne. Pozostają zabezpieczenia w warstwie obsługi płatności.
Nadanie uprawnienia eksportu nie przyznaje automatycznie obsługi faktur.

Zasady dokumentów są niezależne od uprawnień: wystawionej faktury nie edytuje się
wprost; korekta wymaga wystawionego dokumentu pierwotnego. Blokady numerów,
rozpoczętej wysyłki/przyjęcia KSeF, anulowania i rozliczeń nadal obowiązują.
Kosz i przywracanie dokumentów pozostają administracyjne. Wysyłka KSeF wciąż
wymaga skonfigurowanego połączenia i potwierdzenia użytkownika.

UI, odczyt faktur i operacje przekazujące identyfikator użytkownika korzystają
ze wspólnego sprawdzenia modułu. Funkcje DAO bez identyfikatora, przeznaczone
również dla zaufanych skryptów, zachowują zgodność wywołań. Dialogi i operacje
użytkownika przekazują identyfikator także przy tworzeniu/edycji i korektach.
Zmiana roli unieważnia cache bieżącej operacji. Po zmianie uprawnień na innym
stanowisku ponowne zalogowanie odświeża widoczność menu; kolejne odczyty i operacje
już korzystają z aktualnych uprawnień.
