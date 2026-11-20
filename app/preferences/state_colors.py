"""Kolory stanów (błąd, uwaga, OK, informacja) w jednym miejscu.

Wcześniej każdy ekran wpisywał własne jasne barwy (`#fee2e2`, `#991b1b`),
przez co w ciemnym motywie znaczniki były nieczytelne. Tutaj kolor zależy od
motywu: w ciemnym tło jest przygaszone, a tekst i znacznik mocniejszy, żeby
czerwień faktycznie rzucała się w oczy.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# (jasne tło, jasny tekst, ciemne tło, ciemny tekst, kolor znacznika)
_STATES = {
    "error": ("#fee2e2", "#991b1b", "#4a1414", "#ff8a8a", "#dc2626"),
    "warning": ("#fef3c7", "#92400e", "#4a3410", "#fbbf24", "#d97706"),
    "ok": ("#dcfce7", "#166534", "#123a24", "#4ade80", "#16a34a"),
    "info": ("#dbeafe", "#1a5fb4", "#132a4a", "#93c5fd", "#2563eb"),
}


def is_dark(app=None) -> bool:
    """Motyw ciemny, jeśli tło okna jest ciemniejsze niż tekst (paleta aplikacji)."""
    app = app or QApplication.instance()
    if app is None:
        return False
    palette = app.palette()
    window = palette.color(QPalette.ColorRole.Window)
    return window.lightness() < 128


def state_colors(state: str, app=None) -> tuple[QColor, QColor]:
    """Tło i tekst dla stanu w aktualnym motywie."""
    light_bg, light_text, dark_bg, dark_text, _ = _STATES.get(state, _STATES["info"])
    if is_dark(app):
        return QColor(dark_bg), QColor(dark_text)
    return QColor(light_bg), QColor(light_text)


def state_marker(state: str) -> QColor:
    """Kolor znacznika (kropka, ramka) widoczny w obu motywach."""
    return QColor(_STATES.get(state, _STATES["info"])[4])


def paint(item, state: str) -> None:
    """Ustawia tło i tekst komórki tabeli dla danego stanu."""
    background, text = state_colors(state)
    item.setBackground(background)
    item.setForeground(text)
