from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QVariant
from PyQt6.QtGui import QColor
import datetime
import logging

logger = logging.getLogger(__name__)


class MonthlyViewModel(QAbstractTableModel):
    def __init__(self, rows: List[Dict[str, Any]], mode: str, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = rows
        self._mode: str = mode

    def set_rows(self, rows: List[Dict[str, Any]]):
        # szybkie porównanie po id
        old_ids = [r.get("id") for r in self._rows]
        new_ids = [r.get("id") for r in rows]

        if old_ids == new_ids:
            # ta sama struktura — porównaj zawartość i emituj dataChanged dla zmienionych komórek
            self._rows = rows
            if len(rows) == 0:
                return
            top_left = self.index(0, 0)
            bottom_right = self.index(len(rows) - 1, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])
            return

        # struktura zmieniona — reset modelu
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    COL_DOCS = 1
    COL_ETAP = 2

    def _docs_col(self) -> int:
        return self.COL_DOCS

    def _ilosc_col(self) -> int:
        return 3 if self._mode == "ksiegowosc" else -1

    def _uop_cols(self):
        return (3, 4, 5) if self._mode == "kadry" else ()

    DATE_ROLE = Qt.ItemDataRole.UserRole + 1

    @staticmethod
    def _delivered_text(row: Dict[str, Any]) -> str:
        raw = row.get("data_dostarczenia")
        if not row.get("dokumenty_dostarczone") or not raw:
            return ""
        try:
            return datetime.date.fromisoformat(str(raw)[:10]).strftime("%d.%m.%Y")
        except ValueError:
            return str(raw)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 16 if self._mode == "ksiegowosc" else 14

    # kolumny z rozliczenia Saldeo (na końcu modelu, wizualnie przesuwane za „Ilość dokumentów”)
    _RESULT_COLS = {
        "ksiegowosc": {12: ("Przychód", "przychod"), 13: ("Koszty", "koszty"),
                       14: ("VAT", "vat"), 15: ("CIT", "cit")},
        "kadry": {13: ("PIT", "pit")},
    }

    def result_cols(self):
        return sorted(self._RESULT_COLS[self._mode])

    @staticmethod
    def _money(value: Any) -> str:
        if value is None:
            return ""
        return f"{float(value):,.2f}".replace(",", " ").replace(".", ",")

    def _result_cell(self, row: Dict[str, Any], col: int, role: int) -> Any:
        _, key = self._RESULT_COLS[self._mode][col]
        if role == Qt.ItemDataRole.DisplayRole:
            if key in ("vat", "cit", "pit"):
                text = self._money(row.get(f"{key}_kwota"))
                period = str(row.get(f"{key}_okres") or "")
                if text and "kwarta" in period.lower():
                    text += " (kw.)"
                return text
            return self._money(row.get(key))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            parts = []
            if key in ("vat", "cit", "pit"):
                if row.get(f"{key}_okres"):
                    parts.append(f"Okres deklaracji: {row[f'{key}_okres']}")
                if row.get(f"{key}_termin"):
                    parts.append(f"Termin: {row[f'{key}_termin']}")
                if key == "vat" and row.get("vat_opis"):
                    parts.append(f"Różnica VAT: {row['vat_opis']}")
            elif key == "przychod" and row.get("dochod") is not None:
                parts.append(f"Dochód / strata: {self._money(row['dochod'])}")
            return "\n".join(parts) if parts else QVariant()
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._row_background(row)
        return QVariant()

    def _vat_cols(self):
        base = 10 if self._mode == "ksiegowosc" else 11
        return base, base + 1

    _VAT_ICONS = {"czynny": ("✔", "#2e9b4f"), "zwolniony": ("▲", "#d93025"), "nieznany": ("?", "#e69500")}

    def _vat_cell(self, row: Dict[str, Any], col: int, role: int) -> Any:
        prefix = "vat" if col == self._vat_cols()[0] else "vat_ue"
        status = row.get(f"{prefix}_status")
        if role == Qt.ItemDataRole.DisplayRole:
            return self._VAT_ICONS.get(status, ("", ""))[0]
        if role == Qt.ItemDataRole.ForegroundRole and status in self._VAT_ICONS:
            return QColor(self._VAT_ICONS[status][1])
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            from app.utils.vat_check import vat_tooltip
            label = "VAT" if prefix == "vat" else "VAT UE"
            return vat_tooltip(
                label, status, row.get(f"{prefix}_sprawdzono"), row.get(f"{prefix}_sprawdzil")
            )
        return QVariant()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return QVariant()

        row = self._rows[index.row()]
        col = index.column()

        if col in self._vat_cols():
            if role == Qt.ItemDataRole.BackgroundRole:
                return self._row_background(row)
            return self._vat_cell(row, col, role)

        if col in self._RESULT_COLS[self._mode]:
            return self._result_cell(row, col, role)
        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.COL_DOCS:
                return self._delivered_text(row)
            if self._mode == "ksiegowosc":
                mapping = {
                    0: row.get("nazwa", ""),
                    2: row.get("etap", "Brak"),
                    3: str(row.get("ilosc_dok", 0)),
                    4: row.get("czas_ks_str", ""),
                    5: "▶",
                    6: row.get("note_full", row.get("note_snippet", "")),
                    7: "➕",
                    8: "✏️",
                    9: "⋯",
                }
                return mapping.get(col, "")
            mapping = {
                0: row.get("nazwa", ""),
                2: row.get("etap", "Brak"),
                3: str(row.get("uop", 0)),
                4: str(row.get("uz", 0)),
                5: str(row.get("uod", 0)),
                6: row.get("czas_kadry_str", ""),
                7: "▶",
                8: row.get("note_full", row.get("note_snippet", "")),
                9: "➕",
                10: "✏️",
            }
            return mapping.get(col, "")

        if role == Qt.ItemDataRole.CheckStateRole and col == self._docs_col():
            return (
                Qt.CheckState.Checked
                if row.get("dokumenty_dostarczone")
                else Qt.CheckState.Unchecked
            )

        if role == Qt.ItemDataRole.EditRole and col == self.COL_ETAP:
            return row.get("etap", "Brak")

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.BackgroundRole:
            return self._row_background(row)

        return QVariant()

    @staticmethod
    def _row_background(row: Dict[str, Any]) -> Any:
        status = row.get("status", "Aktywne")
        etap = row.get("etap", "Brak")
        if row.get("uslugi_zawieszone"):
            return QColor("#f4a6a6")
        if status == "Zawieszone":
            return Qt.GlobalColor.lightGray
        if status == "Archiwalne":
            return Qt.GlobalColor.gray
        if etap == "Zatwierdzone":
            return Qt.GlobalColor.yellow
        if etap == "Zaksięgowane":
            return Qt.GlobalColor.cyan
        if etap == "Wysłane wyniki":
            return Qt.GlobalColor.green
        return QVariant()

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return QVariant()
        if orientation == Qt.Orientation.Horizontal:
            if self._mode == "ksiegowosc":
                headers = ["Firma", "Dostarczone", "Etap", "Ilość dokumentów", "Czas", "Stoper", "Notatki", "Usługa", "Edytuj", "⋯", "VAT", "EU", "Przychód", "Koszty", "VAT do zapłaty", "CIT"]
            else:
                headers = ["Firma", "Dostarczone", "Etap", "UoP", "UZ", "UoD", "Czas", "Stoper", "Notatki", "Usługa", "Edytuj", "VAT", "EU", "PIT"]
            if 0 <= section < len(headers):
                return headers[section]
        return QVariant()

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == self.COL_ETAP:
            flags |= Qt.ItemFlag.ItemIsEditable
        if index.column() == self._docs_col():
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == self._ilosc_col() or index.column() in self._uop_cols():
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def _persist(self, row: Dict[str, Any], **kwargs) -> None:
        parent = self.parent()
        logic = getattr(parent, "logic", None) if parent else None
        if logic is None:
            return
        try:
            logic.save_status_for_company(row["id"], **kwargs)
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False

        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.CheckStateRole and col == self._docs_col():
            checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value, 2, True)
            row["dokumenty_dostarczone"] = 1 if checked else 0
            row["data_dostarczenia"] = datetime.date.today().isoformat() if checked else None
            self.dataChanged.emit(index, index)
            self._persist(
                row,
                dokumenty_dostarczone=row["dokumenty_dostarczone"],
                data_dostarczenia=row["data_dostarczenia"],
            )
            return True

        if role == self.DATE_ROLE and col == self._docs_col():
            row["data_dostarczenia"] = str(value)
            self.dataChanged.emit(index, index)
            self._persist(row, dokumenty_dostarczone=1, data_dostarczenia=str(value))
            return True

        if role != Qt.ItemDataRole.EditRole:
            return False

        if col == self.COL_ETAP:
            row["etap"] = str(value)
            top = self.index(index.row(), 0)
            bottom = self.index(index.row(), self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)
            self._persist(row, etap=row["etap"])
            return True

        try:
            ivalue = int(str(value).strip() or "0")
        except Exception:
            ivalue = 0

        changed = False

        if self._mode == "ksiegowosc" and col == self._ilosc_col():
            row["ilosc_dok"] = ivalue
            changed = True
        elif self._mode == "kadry" and col in self._uop_cols():
            row[{3: "uop", 4: "uz", 5: "uod"}[col]] = ivalue
            changed = True

        if changed:
            # notify view
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
            # persist via logic if available on parent
            parent = self.parent()
            if parent and hasattr(parent, "logic"):
                try:
                    logic = getattr(parent, "logic")
                    if self._mode == "ksiegowosc":
                        logic.save_status_for_company(row["id"], ilosc_dok=row["ilosc_dok"])
                    elif self._mode == "kadry":
                        logic.save_status_for_company(
                            row["id"],
                            uop=row.get("uop"),
                            uz=row.get("uz"),
                            uod=row.get("uod"),
                        )
                except Exception:
                    # swallow persistence errors here; UI already updated
                    pass
            return True

        return False

    def get_row(self, row_idx: int) -> Optional[Dict[str, Any]]:
        if 0 <= row_idx < len(self._rows):
            return self._rows[row_idx]
        return None
