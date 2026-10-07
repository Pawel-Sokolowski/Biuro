from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, QVariant


class MonthlyViewModel(QAbstractTableModel):
    def __init__(self, rows: List[Dict[str, Any]], mode: str, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = rows
        self._mode: str = mode

    def set_rows(self, rows: List[Dict[str, Any]]):
        """
        Smart update: jeśli liczba wierszy i ich identyfikatory są takie same,
        emitujemy dataChanged dla zmienionych pól zamiast resetować cały model.
        Reset modelu (beginResetModel) wykonujemy tylko gdy struktura się zmienia.
        """
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

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 9 if self._mode == "ksiegowosc" else 10

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return QVariant()

        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if self._mode == "ksiegowosc":
                mapping = {
                    0: row.get("nazwa", ""),
                    1: row.get("etap", "Brak"),
                    2: str(row.get("ilosc_dok", 0)),
                    3: row.get("czas_ks_str", ""),
                    4: "▶",
                    5: row.get("note_snippet", ""),
                    6: "➕",
                    7: "✏️",
                    8: "⋯",
                }
                return mapping.get(col, "")
            else:
                mapping = {
                    0: row.get("nazwa", ""),
                    1: row.get("etap", "Brak"),
                    2: str(row.get("uop", 0)),
                    3: str(row.get("uz", 0)),
                    4: str(row.get("uod", 0)),
                    5: row.get("czas_kadry_str", ""),
                    6: "▶",
                    7: row.get("note_snippet", ""),
                    8: "➕",
                    9: "✏️",
                }
                return mapping.get(col, "")

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.BackgroundRole:
            status = row.get("status", "Aktywne")
            etap = row.get("etap", "Brak")
            if status == "Zawieszone":
                return Qt.GlobalColor.lightGray
            if status == "Archiwalne":
                return Qt.GlobalColor.gray
            if etap == "W trakcie":
                return Qt.GlobalColor.yellow
            if etap == "Zakończone":
                return Qt.GlobalColor.green

        return QVariant()

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return QVariant()
        if orientation == Qt.Orientation.Horizontal:
            if self._mode == "ksiegowosc":
                headers = ["Firma", "Etap", "Ilość dok.", "Czas", "Stoper", "Notatki", "Usługa", "Edytuj", "⋯"]
            else:
                headers = ["Firma", "Etap", "UoP", "UZ", "UoD", "Czas", "Stoper", "Notatki", "Usługa", "Edytuj"]
            if 0 <= section < len(headers):
                return headers[section]
        return QVariant()

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self._mode == "ksiegowosc" and index.column() == 2:
            flags |= Qt.ItemFlag.ItemIsEditable
        if self._mode == "kadry" and index.column() in (2, 3, 4):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False

        row = self._rows[index.row()]
        col = index.column()

        try:
            ivalue = int(str(value).strip() or "0")
        except Exception:
            ivalue = 0

        changed = False

        if self._mode == "ksiegowosc" and col == 2:
            row["ilosc_dok"] = ivalue
            changed = True
        elif self._mode == "kadry" and col in (2, 3, 4):
            if col == 2:
                row["uop"] = ivalue
            elif col == 3:
                row["uz"] = ivalue
            elif col == 4:
                row["uod"] = ivalue
            changed = True

        if changed:
            # notify view
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])
            # persist via logic if available on parent
            parent = self.parent()
            if parent and hasattr(parent, "logic"):
                try:
                    logic = getattr(parent, "logic")
                    if self._mode == "ksiegowosc" and col == 2:
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
