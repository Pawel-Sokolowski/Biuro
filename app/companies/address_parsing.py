"""Rozdzielanie ulicy, numeru budynku i lokalu z danych rejestrowych."""

import re
from typing import Tuple


# Rejestry podają czasem adres "ul. Kwiatowa 23/1" w jednym polu,
# a czasem osobne pola ulicy, budynku i lokalu.
_ADDRESS_SUFFIX = re.compile(
    r"^(?:(?P<street>.*?)\s+)?"
    r"(?P<building>\d+[a-zA-Z]?(?:[-–]\d+[a-zA-Z]?)?)"
    r"(?:\s*/\s*(?P<apartment>\d+[a-zA-Z]?))?$"
)


def split_street_address(
    street: str | None,
    building: str | None = None,
    apartment: str | None = None,
) -> Tuple[str, str, str]:
    """Rozdziela końcowe 23/1, zachowując osobne wartości z rejestru.

    Jeśli odrębne pola są sprzeczne z numerami na końcu ulicy, nie
    zgadujemy poprawnego adresu i zostawiamy oryginalny tekst.
    """
    street_text = " ".join(str(street or "").split())
    building_text = str(building or "").strip()
    apartment_text = str(apartment or "").strip()

    match = _ADDRESS_SUFFIX.fullmatch(street_text)
    if not match:
        return street_text, building_text, apartment_text

    parsed_building = match.group("building")
    parsed_apartment = match.group("apartment") or ""

    if building_text and building_text.casefold() != parsed_building.casefold():
        return street_text, building_text, apartment_text
    if apartment_text and parsed_apartment and apartment_text.casefold() != parsed_apartment.casefold():
        return street_text, building_text, apartment_text

    return (
        (match.group("street") or "").strip(),
        building_text or parsed_building,
        apartment_text or parsed_apartment,
    )
