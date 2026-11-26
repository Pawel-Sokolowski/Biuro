"""Profile editor for the interactive Płatnik agent, inside the installed application."""
import json
import logging
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
        note = QLabel('„Wczytaj z Płatnika” pokazuje kontrolki otwartego rejestru. '
                      'Odczyt UIA wymaga wzorców Grid i Table.')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.executable = QLineEdit(profile.get('executable', ''))
        self.executable.setPlaceholderText(r'C:\Platnik\p2.exe')
        self.launcher = QLineEdit(profile.get('launcher', ''))
        self.launcher.setPlaceholderText(r'C:\Platnik\P2start.exe')
        self.backend = QComboBox()
        self.backend.addItems(('uia', 'win32'))
        self.backend.setCurrentText(profile.get('backend', 'uia'))
        window = profile.get('window') or {}
        self.title = QLineEdit(window.get('title', ''))
        self.title.setPlaceholderText('Dokładny tytuł głównego okna Płatnika')
        self.window_class = QLineEdit(window.get('class_name', ''))
        self.window_class.setPlaceholderText('Użyj klasy, jeśli tytuł zmienia się po wyborze płatnika')
        grid = profile.get('grid') or {}
        grid_value = grid.get('auto_id', '')
        if self.backend.currentText() == 'win32':
            grid_value = grid.get('control_id', grid.get('class_name', ''))
        elif not grid_value and grid.get('class_name'):
            grid_value = 'class=' + grid['class_name']
        self.grid = QLineEdit(str(grid_value))
        self.grid.setPlaceholderText('AutomationId lub class=NazwaKlasy (uia), Control ID lub klasa (win32)')
        for label, widget in (('Plik Płatnika', self.executable), ('Program uruchamiający (opcjonalnie)', self.launcher),
                              ('Interfejs', self.backend), ('Tytuł okna', self.title),
                              ('Klasa okna', self.window_class), ('Tabela rejestru', self.grid)):
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
        self.check_button = QPushButton('Sprawdź odczyt tabeli')
        self.check_button.clicked.connect(self.check_register)
        layout.addWidget(self.check_button)
        self.save_button = QPushButton('Zapisz profil agenta')
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        layout.addStretch()

    def grid_locator(self):
        value = self.grid.text().strip()
        if self.backend.currentText() == 'win32':
            return {'control_id': int(value)} if value.isdecimal() else {'class_name': value}
        if value.startswith('class='):
            return {'class_name': value.removeprefix('class=').strip()}
        return {'auto_id': value}

    def window_config(self):
        window = {'title': self.title.text().strip(), 'class_name': self.window_class.text().strip()}
        return {'executable': self.executable.text().strip(), 'backend': self.backend.currentText(),
                'launcher': self.launcher.text().strip(),
                'window': {key: value for key, value in window.items() if value}}

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
                'grid': self.grid_locator(), 'columns': columns, 'status_labels': labels}

    def describe(self):
        """Run describe in-process against the given EXE; it starts Płatnik if needed."""
        from app.integrations.platnik.gui_agent import validate_config
        from app.integrations.platnik.gui_desktop import describe, main_window
        from app.integrations.platnik.gui_evidence import ERRORS, ReadError

        try:
            config = validate_config(self.window_config(), full=False)
            controls = describe(main_window(config))
        except ReadError as error:
            message = ERRORS[error.code]
            if error.code == 'AppUnavailable':
                message = 'Nie znaleziono okna pasującego do ścieżki oraz podanego tytułu lub klasy Płatnika.'
            QMessageBox.warning(self, 'Agent Płatnika', message)
            return
        except ImportError:
            logging.getLogger(__name__).exception('Płatnik GUI automation import failed')
            QMessageBox.warning(self, 'Agent Płatnika', 'Nie można załadować bibliotek automatyzacji Płatnika. '
                                'Szczegóły zapisano w dzienniku Biuro.')
            return
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, 'Agent Płatnika', str(error))
            return
        except Exception:
            logging.getLogger(__name__).exception('Płatnik control discovery failed')
            QMessageBox.warning(self, 'Agent Płatnika', 'Nie udało się odczytać kontrolek Płatnika. '
                                'Szczegóły zapisano w dzienniku Biuro.')
            return
        lines = []
        for item in controls:
            line = (f"{item['control_type'] or '?'} | class={item['class_name'] or '-'} | "
                    f"auto_id={item['auto_id'] or '-'} | id={item['control_id']}")
            if 'uia_patterns' in item:
                line += ' | UIA: ' + (', '.join(item['uia_patterns']) or 'brak wzorców tabeli')
            lines.append(line)
        self.result.setPlainText('\n'.join(lines) or 'Brak kontrolek.')

    def check_register(self):
        from app.integrations.platnik.gui_agent import validate_config
        from app.integrations.platnik.gui_desktop import main_window, probe_register
        from app.integrations.platnik.gui_evidence import ERRORS, ReadError

        try:
            config = validate_config(self.profile())
            message = probe_register(main_window(config), config)
        except ReadError as error:
            message = error.detail or ERRORS[error.code]
        except (ValueError, OSError, TypeError) as error:
            message = str(error)
        except Exception:
            logging.getLogger(__name__).exception('Płatnik register check failed')
            message = 'Nie udało się sprawdzić odczytu tabeli. Szczegóły zapisano w dzienniku Biuro.'
        self.result.setPlainText(message)

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
