-- Read-only diagnostics. Select the client database in SSMS first.
-- Run once with KPIR in the KPIR database, then with KH in the KH database.
-- Change the month to one whose totals you can compare with Enova.
-- This is NOT a production count_query: it does not guess status/type meanings.
SET NOCOUNT ON;

DECLARE @AccountingType nvarchar(8) = N'KPIR'; -- KPIR or KH
DECLARE @From date = CONVERT(date, '20260601', 112);
DECLARE @To date = DATEADD(month, 1, @From);

IF DB_ID() <= 4
    THROW 50001, N'Select an Enova client database, not a system database.', 1;
IF @AccountingType NOT IN (N'KPIR', N'KH')
    THROW 50002, N'AccountingType must be KPIR or KH.', 1;
IF DAY(@From) <> 1
    THROW 50003, N'From must be the first day of the selected month.', 1;

IF @AccountingType = N'KPIR'
BEGIN
    IF OBJECT_ID(N'dbo.Dziennik', N'U') IS NULL
        THROW 50004, N'Required table dbo.Dziennik is missing.', 1;

    -- Keep states visible until their meanings have been verified.
    -- Entry counts and distinct source-document counts may differ.
    SELECT
        DB_NAME() AS baza,
        CONVERT(char(7), @From, 120) AS miesiac,
        d.Typ,
        d.Bufor,
        d.KPiRStan,
        CASE WHEN d.KPiRLp > 0 THEN 1 ELSE 0 END AS ma_numer_kpir,
        COUNT_BIG(*) AS wpisy_dziennika,
        COUNT_BIG(DISTINCT d.Ewidencja) AS rozne_id_ewidencji,
        MIN(CASE WHEN d.KPiRLp > 0 THEN d.KPiRLp END) AS pierwszy_numer_kpir,
        MAX(CASE WHEN d.KPiRLp > 0 THEN d.KPiRLp END) AS ostatni_numer_kpir
    FROM dbo.Dziennik AS d
    WHERE d.Data >= @From AND d.Data < @To
    GROUP BY d.Typ, d.Bufor, d.KPiRStan,
             CASE WHEN d.KPiRLp > 0 THEN 1 ELSE 0 END
    ORDER BY d.Typ, d.Bufor, d.KPiRStan, ma_numer_kpir;
END;

IF @AccountingType = N'KH'
BEGIN
    IF OBJECT_ID(N'dbo.DokEwidencja', N'U') IS NULL
       OR OBJECT_ID(N'dbo.DefDokumentow', N'U') IS NULL
       OR OBJECT_ID(N'dbo.OperacjeBankowe', N'U') IS NULL
       OR OBJECT_ID(N'dbo.RaportyESP', N'U') IS NULL
       OR OBJECT_ID(N'dbo.Zaplaty', N'U') IS NULL
        THROW 50005, N'Required accounting or bank table is missing.', 1;

    -- Document definitions reveal which entries represent invoices, bank
    -- statement headers, internal documents, etc. Do not add all groups blindly.
    SELECT
        DB_NAME() AS baza,
        CONVERT(char(7), @From, 120) AS miesiac,
        d.Definicja,
        def.Symbol,
        def.Nazwa,
        d.DokumentType,
        d.Typ,
        d.Stan,
        d.BuforK,
        d.NieKsiegowac,
        d.Szablon,
        COUNT_BIG(*) AS dokumenty_ewidencji
    FROM dbo.DokEwidencja AS d
    LEFT JOIN dbo.DefDokumentow AS def ON def.ID = d.Definicja
    WHERE d.DataEwidencji >= @From AND d.DataEwidencji < @To
    GROUP BY d.Definicja, def.Symbol, def.Nazwa, d.DokumentType,
             d.Typ, d.Stan, d.BuforK, d.NieKsiegowac, d.Szablon
    ORDER BY def.Symbol, d.Stan, d.BuforK, d.DokumentType;

    -- Relationships confirmed by the supplied foreign-key metadata:
    -- OperacjeBankowe.WyciagBankowy -> RaportyESP.ID
    -- OperacjeBankowe.Zaplata       -> Zaplaty.ID
    -- Each operation is counted once; statement headers are not divided by 3.
    SELECT
        DB_NAME() AS baza,
        CONVERT(char(7), @From, 120) AS miesiac,
        o.Stan AS stan_operacji,
        CASE WHEN z.ID IS NULL THEN 0 ELSE 1 END AS ma_zaplate,
        z.Stan AS stan_zaplaty,
        r.Bufor AS bufor_wyciagu,
        COUNT_BIG(*) AS pozycje_bankowe
    FROM dbo.OperacjeBankowe AS o
    LEFT JOIN dbo.Zaplaty AS z ON z.ID = o.Zaplata
    LEFT JOIN dbo.RaportyESP AS r ON r.ID = o.WyciagBankowy
    WHERE o.Data >= @From AND o.Data < @To
    GROUP BY o.Stan, CASE WHEN z.ID IS NULL THEN 0 ELSE 1 END,
             z.Stan, r.Bufor
    ORDER BY o.Stan, ma_zaplate, z.Stan, r.Bufor;

    -- Show whether the date basis affects the count for this month.
    SELECT N'Data' AS pole_daty, COUNT_BIG(*) AS pozycje_bankowe
    FROM dbo.OperacjeBankowe
    WHERE Data >= @From AND Data < @To
    UNION ALL
    SELECT N'DataOperacji', COUNT_BIG(*)
    FROM dbo.OperacjeBankowe
    WHERE DataOperacji >= @From AND DataOperacji < @To;
END;
