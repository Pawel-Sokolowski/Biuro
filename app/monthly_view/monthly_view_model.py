from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QVariant
from PyQt6.QtGui import QColor, QFont
import datetime
import logging
from app.companies.company_names import company_display_name
from app.utils.table_sorting import DESCENDING, sort_value

logger = logging.getLogger(__name__)


class MonthlyViewModel(QAbstractTableModel):
    def __init__(self, rows: List[Dict[str, Any]], mode: str, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = rows
        self._mode: str = mode
        self._sort = None
        self.COL_DOCS = 3 if mode == "ksiegowosc" else 1
        self.COL_ETAP = 4 if mode == "ksiegowosc" else 2

    def set_rows(self, rows: List[Dict[str, Any]]):
        if self._sort is not None:
            rows = self._sorted_rows(rows, *self._sort)
        if rows == self._rows:
            return
        # szybkie porównanie po id
        old_ids = [r.get("id") for r in self._rows]
        new_ids = [r.get("id") for r in rows]

        if old_ids == new_ids:
            # ta sama struktura — porównaj zawartość i emituj dataChanged dla zmienionych komórek
            changed = [i for i, (old, new) in enumerate(zip(self._rows, rows)) if old != new]
            self._rows = rows
            if len(rows) == 0:
                return
            for row in changed:
                self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1), [])
            return

        # struktura zmieniona — reset modelu
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    @staticmethod
    def _delivery_date(row):
        if not row.get("dokumenty_dostarczone"):
            return None
        try:
            return datetime.date.fromisoformat(str(row.get("data_dostarczenia") or "")[:10])
        except ValueError:
            return None

    def _delivery_sorted(self, rows, order):
        dated = [row for row in rows if self._delivery_date(row) is not None]
        missing = [row for row in rows if self._delivery_date(row) is None]
        # Stable ties preserve the existing company ordering. Undated rows stay last in either direction.
        return sorted(dated, key=self._delivery_date, reverse=order == Qt.SortOrder.DescendingOrder) + missing

    def _sorted_rows(self, rows, column, order):
        if column == self._docs_col():
            return self._delivery_sorted(rows, order)
        return sorted(rows, key=lambda row: sort_value(self._display_value(row, column)),
                      reverse=order == DESCENDING)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if not 0 <= column < self.columnCount():
            return
        self._sort = column, order
        rows = self._sorted_rows(self._rows, column, order)
        if rows == self._rows:
            return
        self.layoutAboutToBeChanged.emit()
        persistent = self.persistentIndexList()
        identities = [id(self._rows[index.row()]) for index in persistent]
        self._rows = rows
        positions = {id(row): index for index, row in enumerate(self._rows)}
        self.changePersistentIndexList(persistent, [self.index(positions[identity], index.column())
                                                   for identity, index in zip(identities, persistent)])
        self.layoutChanged.emit()

    def _sort_after_edit(self, column):
        if self._sort is not None and self._sort[0] == column:
            self.sort(*self._sort)

    COL_DOCS = 1
    COL_ETAP = 2

    def _docs_col(self) -> int:
        return self.COL_DOCS

    def _etap_col(self) -> int:
        return self.COL_ETAP

    def _ilosc_col(self) -> int:
        return 5 if self._mode == "ksiegowosc" else -1

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
        return 14 if self._mode == "ksiegowosc" else 13

    def _vat_cols(self):
        return (12, 13) if self._mode == "ksiegowosc" else ()

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

    def _display_value(self, row, col):
        if col == 0:
            return company_display_name(row)
        if col == self._docs_col():
            return self._delivered_text(row)
        if col in self._vat_cols():
            key = 'vat_status' if col == self._vat_cols()[0] else 'vat_ue_status'
            return row.get(key)
        shared = {7: "▶", 8: row.get("note_full", row.get("note_snippet", "")), 9: "➕", 10: "✏️"}
        if self._mode == "ksiegowosc":
            values = {1: row.get("numer_faktury") or "", 2: row.get("faktura_zaplacona") or "",
                      4: row.get("etap", "Brak"), 5: str(row.get("ilosc_dok", 0)),
                      6: row.get("czas_ks_str", ""), 11: "⋯"}
        else:
            values = {2: row.get("etap_kadry", "Brak"), 3: str(row.get("uop", 0)), 4: str(row.get("uz", 0)),
                      5: str(row.get("uod", 0)), 6: row.get("czas_kadry_str", ""),
                      11: row.get('insurance_codes') or '—', 12: row.get('insurance_deadline') or '—'}
        return shared.get(col, values.get(col, ""))

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return QVariant()

        row = self._rows[index.row()]
        col = index.column()

        if col == 0 and row.get("zastepstwo"):
            if role == Qt.ItemDataRole.DisplayRole:
                return "↪ " + company_display_name(row)
            if role == Qt.ItemDataRole.ToolTipRole:
                who = row.get("zastepstwo_za")
                return f"Zastępstwo za: {who}" if who else "Zastępstwo"
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#1a5fb4")
            if role == Qt.ItemDataRole.FontRole:
                font = QFont()
                font.setItalic(True)
                return font

        if self._mode == "ksiegowosc" and col in (1, 2) and role == Qt.ItemDataRole.ToolTipRole:
            return row.get("faktura_platnosci_tooltip", "")

        if self._mode == 'kadry' and col == 12 and role == Qt.ItemDataRole.ToolTipRole:
            return ('Prognoza limitu przy dalszym korzystaniu wg historii Płatnika. '
                    'Sprawdź warunki roczne i ewentualny limit dodatkowy Małego ZUS Plus.\n'
                    + (row.get('insurance_details') or 'Brak danych o okresach ubezpieczenia.'))

        if self._mode == 'kadry' and col == 11 and role == Qt.ItemDataRole.ToolTipRole:
            return row.get('insurance_details') or 'Brak kodów z dokumentów Płatnika za ten miesiąc. Zleć pobranie formularzy ZUS.'

        if col in self._vat_cols():
            if role == Qt.ItemDataRole.BackgroundRole:
                return self._row_background(row)
            return self._vat_cell(row, col, role)

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(row, col)

        if role == Qt.ItemDataRole.CheckStateRole and col == self._docs_col():
            return (
                Qt.CheckState.Checked
                if row.get("dokumenty_dostarczone")
                else Qt.CheckState.Unchecked
            )

        if role == Qt.ItemDataRole.EditRole and col == self._etap_col():
            return row.get(self._etap_key(), "Brak")

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.BackgroundRole:
            return self._row_background(row)

        return QVariant()

    def _etap_key(self) -> str:
        return "etap_kadry" if self._mode == "kadry" else "etap"

    def _row_background(self, row: Dict[str, Any]) -> Any:
        status = row.get("status", "Aktywne")
        etap = row.get(self._etap_key(), "Brak")
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
        if orientation == Qt.Orientation.Horizontal and section == self._docs_col() and role == Qt.ItemDataRole.ToolTipRole:
            return "Kliknij dwukrotnie, aby sortować według daty dostarczenia."
        if role != Qt.ItemDataRole.DisplayRole:
            return QVariant()
        if orientation == Qt.Orientation.Horizontal:
            if self._mode == "ksiegowosc":
                headers = ["Firma", "Nr faktury", "Zapłacona", "Komplet", "Etap", "Ilość dokumentów", "Czas", "Stoper", "Notatki", "Usługa", "Edytuj", "⋯", "VAT", "EU"]
            else:
                headers = ["Firma", "Komplet", "Etap", "UoP", "UZ", "UoD", "Czas", "Stoper", "Notatki", "Usługa", "Edytuj",
                           "Kody ubezpieczenia", "Ulga ZUS do"]
            if 0 <= section < len(headers):
                return headers[section]
        return QVariant()

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == self._etap_col():
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
            self._sort_after_edit(col)
            return True

        if role == self.DATE_ROLE and col == self._docs_col():
            row["data_dostarczenia"] = str(value)
            row["dokumenty_dostarczone"] = 1
            self.dataChanged.emit(index, index)
            self._persist(row, dokumenty_dostarczone=1, data_dostarczenia=str(value))
            self._sort_after_edit(col)
            return True

        if role != Qt.ItemDataRole.EditRole:
            return False

        if col == self._etap_col():
            key = self._etap_key()
            row[key] = str(value)
            top = self.index(index.row(), 0)
            bottom = self.index(index.row(), self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)
            self._persist(row, **{key: row[key]})
            self._sort_after_edit(col)
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
            self._sort_after_edit(col)
            return True

        return False

    def get_row(self, row_idx: int) -> Optional[Dict[str, Any]]:
        if 0 <= row_idx < len(self._rows):
            return self._rows[row_idx]
        return None
