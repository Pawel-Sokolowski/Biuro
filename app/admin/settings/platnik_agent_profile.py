"""Profile editor for the interactive Płatnik agent, inside the installed application."""
import json
from pathlib import Path
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, QPushButton,
                             QLabel, QPlainTextEdit, QMessageBox)

COLUMNS = ('nip', 'period', 'form', 'report', 'set_id', 'status', 'receipt', 'sent_at')


def load_profile(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class PlatnikAgentProfile(QWidget):
    """Write %LOCALAPPDATA%\\Biuro\\platnik-agent.json without hand-editing JSON."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from app.integrations.platnik.gui_agent import default_profile
        self.path = default_profile()
        profile = load_profile(self.path)
        layout = QVBoxLayout(self)
        note = QLabel('Profil agenta Płatnika. Agent uruchamia Płatnika sam i odczytuje rejestr po zalogowaniu '
                      'na pulpicie serwera. „Wczytaj z Płatnika” pokazuje typy i identyfikatory kontrolek — '
                      'skopiuj z wyniku AutomationId tabeli rejestru.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.executable = QLineEdit(profile.get('executable', ''))
        self.executable.setPlaceholderText(r'C:\Platnik\Płatnik.exe')
        self.backend = QComboBox()
        self.backend.addItems(('uia', 'win32'))
        self.backend.setCurrentText(profile.get('backend', 'uia'))
        self.title = QLineEdit((profile.get('window') or {}).get('title', ''))
        self.title.setPlaceholderText('Dokładny tytuł głównego okna Płatnika')
        self.grid = QLineEdit((profile.get('grid') or {}).get('auto_id', ''))
        self.grid.setPlaceholderText('AutomationId tabeli rejestru (z „Wczytaj z Płatnika”)')
        for label, widget in (('Plik Płatnika', self.executable), ('Interfejs', self.backend),
                              ('Tytuł okna', self.title), ('Tabela rejestru', self.grid)):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.columns = {}
        for name in COLUMNS:
            column = (profile.get('columns') or {}).get(name) or {}
            index, header = QLineEdit(str(column.get('index', ''))), QLineEdit(column.get('header', ''))
            index.setPlaceholderText('0')
            header.setPlaceholderText('Nagłówek kolumny')
            self.columns[name] = (index, header)
            form.addRow('Kolumna ' + name, index)
            form.addRow('Nagłówek ' + name, header)
        self.status_labels = QPlainTextEdit('\n'.join(
            f'{label} = {status}' for label, status in (profile.get('status_labels') or {}).items()))
        self.status_labels.setPlaceholderText('Dokładna etykieta stanu = sent / accepted / rejected')
        self.status_labels.setFixedHeight(80)
        form.addRow('Etykiety stanów', self.status_labels)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setFixedHeight(110)
        layout.addWidget(self.result)
        self.describe_button = QPushButton('Wczytaj z Płatnika…')
        self.describe_button.clicked.connect(self.describe)
        layout.addWidget(self.describe_button)
        self.save_button = QPushButton('Zapisz profil agenta')
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch()

    def profile(self):
        columns = {}
        for name, (index, header) in self.columns.items():
            text = index.text().strip()
            if text and header.text().strip():
                columns[name] = {'index': int(text), 'header': header.text().strip()}
        labels = {}
        for line in self.status_labels.toPlainText().splitlines():
            if '=' in line:
                label, status = line.split('=', 1)
                if label.strip() and status.strip():
                    labels[label.strip()] = status.strip()
        value = {'executable': self.executable.text().strip(), 'backend': self.backend.currentText(),
                 'window': {'title': self.title.text().strip()}, 'tabs': [],
                 'grid': {'auto_id': self.grid.text().strip()}, 'columns': columns,
                 'status_labels': labels}
        if value['backend'] == 'win32':
            value['grid'] = {'class_name': self.grid.text().strip()}
        return value

    def describe(self):
        """Run describe in-process against the given EXE; it starts Płatnik if needed."""
        try:
            from app.integrations.platnik.gui_desktop import describe
            from app.integrations.platnik.gui_evidence import ReadError
            config = self.profile()
            if not config['executable'] or not config['window']['title']:
                raise ValueError('Podaj plik Płatnika i dokładny tytuł okna.')
            controls = describe({'executable': Path(config['executable']),
                                 'backend': config['backend'], 'window': config['window']})
        except ReadError:
            QMessageBox.warning(self, 'Agent Płatnika', 'Płatnik nie jest dostępny na tym pulpicie. '
                                'Zaloguj się na pulpicie serwera, otwórz Płatnika i spróbuj ponownie.')
            return
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Agent Płatnika', str(error))
            return
        except Exception:
            QMessageBox.warning(self, 'Agent Płatnika', 'Nie udało się odczytać kontrolek Płatnika.')
            return
        self.result.setPlainText('\n'.join(
            f"{item['control_type'] or '?'} | class={item['class_name'] or '-'} | "
            f"auto_id={item['auto_id'] or '-'} | id={item['control_id']}" for item in controls) or 'Brak kontrolek.')

    def save(self):
        try:
            value = self.profile()
            if not value['executable'] or not value['window']['title'] or not value['grid']:
                raise ValueError('Uzupełnij plik Płatnika, tytuł okna i tabelę rejestru.')
            if not value['columns'] or not value['status_labels']:
                raise ValueError('Uzupełnij kolumny rejestru i etykiety stanów.')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ValueError, OSError, TypeError) as error:
            QMessageBox.warning(self, 'Agent Płatnika', str(error))
            return
        QMessageBox.information(self, 'Agent Płatnika', 'Zapisano profil: ' + str(self.path))
