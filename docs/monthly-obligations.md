# Rozliczenia miesięczne i kwartalne firmy

W formularzu firmy, obok rozliczeń rocznych, znajduje się **Rozliczenia
miesięczne i kwartalne**. Wybierz miesiąc, od którego obowiązuje konfiguracja,
częstotliwość VAT oraz zaznacz dotyczące firmy raporty: JPK VAT, VAT-UE,
zaliczki PIT/CIT, DRA, RCA, RSA, RPA i RZA. Zaliczki PIT/CIT mają osobny wybór
miesięczny/kwartalny. Odznaczenie raportu wyłącza firmę z tego raportu w Sprawdzaniu.

Przykład firmy rozliczającej wyłącznie DRA: pozostaw DRA, odznacz RCA/RSA/RPA/RZA.
Firma nie pojawi się w widoku RCA ani jako brakujący RCA w filtrze uwagi,
także gdy w bazie zachowały się stare dokumenty RCA. Zmiana zakresu ich nie usuwa.
Bez konfiguracji istniejące firmy zachowują dotychczasową widoczność raportów.

VAT nieczynny/zwolniony wyłącza firmę z JPK VAT na podstawie Statusu VAT firmy.
Brak danych o statusie pozostaje widoczny do sprawdzenia. Jawny Status VAT ma
pierwszeństwo przed starszym polem `vat_czynny`. Ustawienie „Nie dotyczy” przy
VAT albo odznaczenie JPK również wyłącza firmę z tego raportu. Nie dotyczy to
odrębnych raportów, np. JPK CIT.

Przy kwartalnym VAT widok części deklaracyjnej **VAT** dotyczy marca, czerwca,
września i grudnia. **JPK VAT — ewidencja** pozostaje miesięczny, również dla
V7K; część deklaracyjna jest składana za kwartał. Wynika to z
[instrukcji Ministerstwa Finansów](https://podatki.gov.pl/podatki-firmowe/jednolity-plik-kontrolny/jpk_vat-z-deklaracja/jpk_vat-z-deklaracja).
Ustawienie częstotliwości nie tworzy statusu wysłania ani fikcyjnego odczytu VAT.

Konfiguracja obowiązuje od wybranego miesiąca do następnej zmiany. Zmiany są
przechowywane we wspólnej bazie, z kontrolą uprawnień edycji firmy i audytem.
Otwieranie formularza bez zmiany ustawień niczego nie zapisuje. Roczna lista
obowiązków jest odrębna i pozostaje bez zmian.
