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
        profile = self.saved_profile = load_profile(self.path)
        layout = QVBoxLayout(self)
        note = QLabel('Profil agenta Płatnika. Agent uruchamia Płatnika sam i odczytuje rejestr po zalogowaniu '
                      'na pulpicie serwera. „Wczytaj z Płatnika” pokazuje typy i identyfikatory kontrolek — '
                      'skopiuj z wyniku AutomationId (uia) lub klasę tabeli rejestru (win32).')
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
        self.grid = QLineEdit((profile.get('grid') or {}).get(self.grid_key(), ''))
        self.grid.setPlaceholderText('AutomationId (uia) lub klasa (win32)')
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

    def grid_key(self):
        return 'class_name' if self.backend.currentText() == 'win32' else 'auto_id'

    def window_config(self):
        return {'executable': self.executable.text().strip(), 'backend': self.backend.currentText(),
                'window': {**(self.saved_profile.get('window') or {}), 'title': self.title.text().strip()}}

    def profile(self):
        columns = {}
        for name, (index, header) in self.columns.items():
            text = index.text().strip()
            heading = header.text().strip()
            if text or heading:
                if not text.isdecimal() or not heading:
                    raise ValueError(f'Podaj nieujemny indeks i nagłówek kolumny {name}.')
                columns[name] = {'index': int(text), 'header': heading}
        labels = {}
        for line in self.status_labels.toPlainText().splitlines():
            if not line.strip():
                continue
            label, _, status = (part.strip() for part in line.partition('='))
            if not label or not status or label in labels:
                raise ValueError('Podaj odrębne etykiety stanów w formacie etykieta = stan.')
            labels[label] = status
        return {'tabs': [], **self.saved_profile, **self.window_config(),
                'grid': {self.grid_key(): self.grid.text().strip()}, 'columns': columns, 'status_labels': labels}

    def describe(self):
        """Run describe in-process against the given EXE; it starts Płatnik if needed."""
        from app.integrations.platnik.gui_agent import validate_config
        from app.integrations.platnik.gui_desktop import describe, main_window
        from app.integrations.platnik.gui_evidence import ReadError

        try:
            config = validate_config(self.window_config(), full=False)
            controls = describe(main_window(config))
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
        from app.integrations.platnik.gui_agent import validate_config

        try:
            value = self.profile()
            validate_config(value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        except (ValueError, OSError, TypeError) as error:
            QMessageBox.warning(self, 'Agent Płatnika', str(error))
            return
        self.saved_profile = value
        QMessageBox.information(self, 'Agent Płatnika', 'Zapisano profil: ' + str(self.path))
