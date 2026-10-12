from PyQt6.QtCore import Qt

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
)

from app.admin.global_settings_logic import GlobalSettingsLogic


class UserCompaniesDialog(QDialog):

    def __init__(
        self,
        user_id: int,
        parent=None,
    ):
        super().__init__(parent)

        self.user_id = user_id
        self.logic = GlobalSettingsLogic()

        self.setWindowTitle("Przypisane firmy")
        self.resize(900, 700)

        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(
            [
                "Przypisany",
                "Firma",
            ]
        )

        self.table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch,
        )

        layout.addWidget(self.table)

        top = QHBoxLayout()

        btn_all = QPushButton(
            "Zaznacz wszystkie"
        )

        btn_none = QPushButton(
            "Odznacz wszystkie"
        )

        btn_all.clicked.connect(
            self._check_all
        )

        btn_none.clicked.connect(
            self._uncheck_all
        )

        top.addWidget(btn_all)
        top.addWidget(btn_none)
        top.addStretch()

        layout.addLayout(top)

        bottom = QHBoxLayout()

        btn_save = QPushButton(
            "Zapisz"
        )

        btn_cancel = QPushButton(
            "Anuluj"
        )

        btn_save.clicked.connect(
            self._save
        )

        btn_cancel.clicked.connect(
            self.reject
        )

        bottom.addStretch()
        bottom.addWidget(btn_save)
        bottom.addWidget(btn_cancel)

        layout.addLayout(bottom)

        self._load()

    def _load(self):

        companies = self.logic.get_companies()

        assigned = set(
            self.logic.get_companies_for_user(
                self.user_id
            )
        )

        self.table.setRowCount(0)

        for company in companies:

            row = self.table.rowCount()

            self.table.insertRow(row)

            chk = QTableWidgetItem()

            chk.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
            )

            chk.setCheckState(
                Qt.CheckState.Checked
                if company["id"] in assigned
                else Qt.CheckState.Unchecked
            )

            chk.setData(
                Qt.ItemDataRole.UserRole,
                company["id"],
            )

            self.table.setItem(
                row,
                0,
                chk,
            )

            self.table.setItem(
                row,
                1,
                QTableWidgetItem(
                    company["nazwa"]
                ),
            )

    def _check_all(self):

        for row in range(
            self.table.rowCount()
        ):
            self.table.item(
                row,
                0,
            ).setCheckState(
                Qt.CheckState.Checked
            )

    def _uncheck_all(self):

        for row in range(
            self.table.rowCount()
        ):
            self.table.item(
                row,
                0,
            ).setCheckState(
                Qt.CheckState.Unchecked
            )

    def _save(self):

        selected = []

        for row in range(
            self.table.rowCount()
        ):

            item = self.table.item(
                row,
                0,
            )

            if (
                item.checkState()
                == Qt.CheckState.Checked
            ):
                selected.append(
                    int(
                        item.data(
                            Qt.ItemDataRole.UserRole
                        )
                    )
                )

        self.logic.save_companies_for_user(
            self.user_id,
            selected,
        )

        QMessageBox.information(
            self,
            "OK",
            "Przypisania zapisane.",
        )

        self.accept()