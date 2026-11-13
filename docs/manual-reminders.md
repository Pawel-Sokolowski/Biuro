# Ręczne przypomnienia i rozliczenia MT940 (1.5.3)

W **Księgowość/Kadry → Widok miesięczny** wybierz miesiąc, zaznacz jedną firmę
i kliknij **Przypomnij o dokumentach…**. Wiadomość dotyczy wskazanego miesiąca,
także archiwalnego; termin wynika z ustawionego dnia w kolejnym miesiącu.
Przycisku nie ma w widoku całego roku. Oznaczony komplet dokumentów zatrzymuje
przypomnienie, również gdy status zmieni się podczas edycji wiadomości.

W **Faktury → Wystawione** zaznacz jedną fakturę i kliknij **Przypomnij o płatności…**.
Przypomnienie jest dostępne dla wystawionych faktur i zaliczek z dodatnim saldem
i poprawnym terminem płatności. Uwzględnia częściową zapłatę. Anulowane,
usunięte do kosza i w pełni zapłacone faktury nie są wysyłane. Jeśli dane faktury,
kwota lub adres klienta zmienią się po otwarciu wiadomości, otwórz ją ponownie.

Oba przyciski otwierają zwykłe okno tworzenia wiadomości z zapisanym szablonem.
Można poprawić odbiorców, temat, treść i wybrać przypisane konto nadawcze.
Pracownik potrzebuje dostępu do odpowiedniego modułu i konta pocztowego;
nie wymaga uprawnień administratora ani osobnego dostępu do finansów.
Wysyłka następuje dopiero po **Wyślij**, w tle. Kopia trafia do Wysłanych;
niepotwierdzony zapis IMAP jest sygnalizowany bez ponawiania dostarczonego maila.

Ręczne przypomnienia działają także przy wyłączonym harmonogramie automatycznym.
Program zapisuje próbę przed wysłaniem, wspólnie dla wszystkich komputerów.
Dla tej samej firmy i miesiąca albo faktury można podjąć jedną ręczną próbę dziennie.
Po dzisiejszej próbie automatycznej ręczna jest zatrzymywana. Worker pomija tego
dnia przypomnienie, które obsłużono ręcznie, również przy niepewnym wyniku SMTP.
Sprawdź Wysłane przed ponowną wysyłką; nie ma automatycznego ponawiania.

Okno **Rozlicz MT940** domyślnie pokazuje nierozliczone wpływy PLN. Przelew znika
z kolejki po przypisaniu całej kwoty; częściowo rozliczony pozostaje z resztą.
Zapis importu i rozliczenia pozostają w historii. Przełącznik **Pokaż wszystkie
zaimportowane operacje, także rozliczone i wypływy** umożliwia ich podgląd.
