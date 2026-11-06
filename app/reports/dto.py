"""Stable report DTOs, carrying identity separately from display labels."""
from typing import TypedDict


class ProfitRow(TypedDict):
    firma_id: int
    klient: str
    miesiac: str
    sekcja: str
    pracownik_id: int | None
    pracownik: str
    ilosc: int
    sek_glowny: int
    cena_glowna: float
    sek_dodatkowy: int
    cena_dodatkowa: float
    sekundy: int
    cena: float
    koszt: float
    zysk: float
