"""Annual filing checklist; symbols are stable, labels describe the annual scope.

Official sources and review scope: docs/annual-declarations.md.
This catalogue tracks obligations, rather than selecting tax regimes or filing forms.
"""

ANNUAL_DECLARATIONS = [
    ("PIT — rozliczenia podatnika", [
        ("PIT-28", "PIT-28", "Rozliczenie ryczałtu od przychodów ewidencjonowanych."),
        ("PIT-36", "PIT-36", "Zeznanie podatnika rozliczającego się według skali."),
        ("PIT-36L", "PIT-36L", "Zeznanie podatnika rozliczającego podatek liniowy."),
        ("PIT-37", "PIT-37", "Roczne rozliczenie dochodów uzyskanych za pośrednictwem płatników."),
        ("PIT-38", "PIT-38", "Roczne rozliczenie wybranych dochodów kapitałowych."),
        ("PIT-39", "PIT-39", "Rozliczenie odpłatnego zbycia nieruchomości i praw majątkowych."),
        ("PIT-16A", "PIT-16A", "Roczna deklaracja dotycząca składki zdrowotnej przy karcie podatkowej."),
        ("DSF-1", "DSF-1", "Deklaracja daniny solidarnościowej."),
    ]),
    ("CIT — rozliczenia podatnika", [
        ("CIT-8", "CIT-8", "Roczne zeznanie podatnika CIT."),
        ("CIT-8E", "CIT-8E", "Roczna deklaracja podatnika CIT estońskiego."),
        ("CIT-8AB", "CIT-8AB", "Roczne zeznanie podatkowej grupy kapitałowej."),
        ("CIT-8FR", "CIT-8FR", "Deklaracja podatku fundacji rodzinnej."),
        ("CIT-D", "CIT-D", "Informacja o darowiznach, jeżeli występuje taki obowiązek."),
    ]),
    ("Płatnik — informacje i deklaracje roczne", [
        ("PIT-4R", "PIT-4R", "Roczne zestawienie zaliczek płatnika."),
        ("PIT-8AR", "PIT-8AR", "Roczne rozliczenie zryczałtowanego podatku pobranego przez płatnika."),
        ("PIT-11", "PIT-11", "Informacje o dochodach podatników i pobranych zaliczkach."),
        ("PIT-8C", "PIT-8C", "Informacje o wybranych dochodach kapitałowych."),
        ("PIT-R", "PIT-R", "Informacja o kwotach z tytułu obowiązków społecznych i obywatelskich."),
        ("IFT-1R", "IFT-1R", "Roczna informacja o przychodach nierezydentów — osób fizycznych."),
        ("IFT-2R", "IFT-2R", "Roczna informacja o przychodach nierezydentów — podatników CIT."),
        ("CIT-6R", "CIT-6R", "Roczna deklaracja płatnika dotycząca wybranych wypłat krajowym podatnikom CIT."),
        ("CIT-6AR", "CIT-6AR", "Roczna deklaracja dotycząca dochodów z udziału w zyskach osób prawnych."),
        ("CIT-10Z", "CIT-10Z", "Roczna deklaracja płatnika dotycząca podatku pobranego od nierezydentów CIT."),
    ]),
    ("ZUS — obowiązki roczne", [
        ("ZUS DRA — roczne zdrowotne", "ZUS DRA — roczne zdrowotne", "Roczne rozliczenie składki zdrowotnej w bloku XII ZUS DRA za kwiecień następnego roku. Wybierz rok, którego dotyczy rozliczenie. Nie jest to zwykła miesięczna DRA."),
        ("ZUS RCA — roczne zdrowotne", "ZUS RCA — roczne zdrowotne", "Roczne rozliczenie składki zdrowotnej w bloku III.F ZUS RCA za kwiecień następnego roku. Wybierz rok, którego dotyczy rozliczenie. To alternatywna ścieżka do DRA, zależna od sposobu rozliczania płatnika."),
        ("ZUS IWA", "ZUS IWA", "Roczna informacja do ustalenia składki wypadkowej — dla zobowiązanych płatników."),
        ("ZUS ZSWA", "ZUS ZSWA", "Roczne zgłoszenie pracy w szczególnych warunkach lub o szczególnym charakterze — gdy występuje obowiązek."),
        ("ZUS — informacja roczna", "Informacja roczna dla ubezpieczonego", "Roczna informacja dla osoby ubezpieczonej, jeżeli płatnik ma obowiązek jej przekazania."),
    ]),
    ("PFRON — dokumenty roczne", [
        ("PFRON DEK-R", "PFRON DEK-R", "Roczna deklaracja wpłat na PFRON — dla zobowiązanych pracodawców."),
        ("PFRON INF-2", "PFRON INF-2", "Roczna informacja pracodawcy zwolnionego z wpłat na PFRON — gdy dotyczy."),
    ]),
]

DECLARATION_DETAILS = {code: (label, description) for _, rows in ANNUAL_DECLARATIONS for code, label, description in rows}
