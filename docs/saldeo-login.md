# Logowanie przeglądarkowe Saldeo

W lokalnym panelu workera na serwerze wpisz login i hasło Saldeo, następnie
wybierz otwarcie przeglądarki. Przycisk zapisuje formularz przed uruchomieniem
osobnego procesu. Hasło pozostaje zaszyfrowane na serwerze; nie trafia do
argumentów procesu ani logów. Profil przeglądarki należy do konta Windows workera.

Okno próbuje jednokrotnie wypełnić i wysłać jednoznaczny, widoczny formularz
na skonfigurowanym adresie HTTPS. Puste selektory mogą zostać rozpoznane
z widocznych kontrolek, bez odczytywania wpisanych wartości. Nie ma powtarzania
prób z hasłem. Brak danych lub jednoznacznych kontrolek pozostawia logowanie
ręczne; 2FA/CAPTCHA również wymaga działania użytkownika.

Po pojawieniu się skonfigurowanego znacznika zalogowanej strony sesja jest
potwierdzana i okno automatycznie się zamyka. Zamknięcie okna wcześniej lub
upływ 15 minut daje status niepotwierdzonej sesji. Samo uruchomienie przeglądarki
nie jest zgłaszane jako udane połączenie. Monitoring historii wymaga dodatkowo
prawidłowych selektorów listy i paginacji; potwierdzenie logowania nie potwierdza
jeszcze kompletnego odczytu historii.
