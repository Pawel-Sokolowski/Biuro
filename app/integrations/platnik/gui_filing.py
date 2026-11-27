"""Continue a Płatnik wizard after the operator selects the sets in Płatnik."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import re
from time import monotonic, sleep

from app.integrations.platnik.gui_desktop import dialog_button, interactive_desktop, process_image
from app.integrations.platnik.gui_diagnostic_capture import discover_executable
from app.integrations.platnik.gui_evidence import ReadError
from app.integrations.platnik.gui_pin import read_pin


WIZARD_TITLE = re.compile(
    r'(Kreator (?:grupowego )?(?:wysyłania zestawów do ZUS|potwierdzania zestawów))'
    r'\s*[-–—]\s*krok\s*(\d+)\s*/\s*(\d+)', re.IGNORECASE,
)
NEXT = ('Dalej >', 'Dalej')


@dataclass(frozen=True)
class Wizard:
    mode: str
    first: int
    last: int
    next_steps: frozenset[int]
    report: tuple[int, str] | None = None
    pin_steps: frozenset[int] = frozenset()
    pin_field_step: int | None = None


# The sending reports distinguish the actual transmission step from earlier
# updates and validation steps. The receipt paths have their own wizard IDs.
WIZARDS = {
    'kreator wysyłania zestawów do zus': Wizard('send', 1, 9, frozenset({1, 2, 3, 4, 5, 7}),
                                               (7, 'Raport z podpisywania'), frozenset({2, 5, 6}), 5),
    'kreator grupowego wysyłania zestawów do zus': Wizard('send', 2, 10, frozenset({2, 3, 5, 6, 8}),
                                                         (8, 'Raport z podpisywania'), frozenset({2, 3, 6, 7}), 6),
    'kreator potwierdzania zestawów': Wizard('receipt', 1, 5, frozenset({1, 3}),
                                            (3, 'Raport z pobierania')),
    'kreator grupowego potwierdzania zestawów': Wizard('receipt', 2, 6, frozenset({2, 4}),
                                                     (4, 'Raport z pobierania')),
}


def identify(window):
    if window.element_info.class_name != 'TfKreatorWysylki':
        return None
    match = WIZARD_TITLE.fullmatch(window.window_text())
    if not match:
        return None
    wizard = WIZARDS[match[1].casefold()]
    step, total = int(match[2]), int(match[3])
    if total == wizard.last and 1 <= step <= total:
        return wizard, step
    return None


def desktop_windows():
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    from pywinauto import Desktop
    return Desktop(backend='win32').windows(visible_only=True)


@contextmanager
def automation():
    import pythoncom

    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    except pythoncom.com_error as error:
        if error.hresult != -2147417850:  # RPC_E_CHANGED_MODE
            raise
        yield
        return
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def start(mode):
    from app.integrations.platnik.gui_agent import default_profile, load_config

    profile = default_profile()
    executable = load_config(profile, full=False)['executable'] if profile.is_file() else discover_executable()
    matches = []
    for window in desktop_windows():
        if (found := identify(window)) and process_image(window.process_id()) == executable:
            matches.append((window, *found))
    if len(matches) != 1:
        raise ValueError('Otwórz jeden kreator wysyłki lub pobierania potwierdzeń w Płatniku.')
    window, wizard, step = matches[0]
    if wizard.mode != mode or step != wizard.first or not window.is_enabled() or not dialog_button(window, *NEXT):
        raise ValueError('W Płatniku wybierz zestawy w odpowiednim kreatorze. Zatrzymaj się, gdy przycisk „Dalej” jest aktywny.')
    return FilingSession(executable, window.process_id(), window.handle, wizard, step)


def press_button(window, button):
    """Post to the verified child window so a new modal does not block the reader."""
    import win32con
    import win32gui

    handle = button.handle
    if (not interactive_desktop() or not handle or not win32gui.IsChild(window.handle, handle)
            or not win32gui.IsWindowVisible(handle) or not win32gui.IsWindowEnabled(handle)):
        raise ReadError('ScreenUnavailable')
    classname = win32gui.GetClassName(handle)
    if classname in ('Button', 'TButton'):
        win32gui.PostMessage(handle, win32con.BM_CLICK, 0, 0)
        return
    if classname != 'TCorelButton':
        raise ReadError('ProfileMismatch')
    left, top, right, bottom = win32gui.GetClientRect(handle)
    point = ((top + bottom) // 2 << 16) | ((left + right) // 2)
    win32gui.PostMessage(handle, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point)
    win32gui.PostMessage(handle, win32con.WM_LBUTTONUP, 0, point)


def pin_fields(window):
    return [field for classname in ('TMaskEdit', 'TEdit') for field in window.descendants(class_name=classname)
            if field.is_visible() and field.is_enabled() and field.style() & 0x20]  # ES_PASSWORD


def write_pin_field(field, pin):
    from ctypes import WinDLL, addressof, byref, c_size_t, create_unicode_buffer, memset, sizeof, wintypes

    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    send = WinDLL('user32', use_last_error=True).SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                     wintypes.UINT, wintypes.UINT, wintypes.LPVOID]
    send.restype = wintypes.LPARAM
    secret = create_unicode_buffer(pin)
    written = c_size_t()
    try:
        if not send(field.handle, 0x000C, 0, addressof(secret), 0x0002, 3000, byref(written)) or not written.value:
            raise ReadError('ReadFailed')
    finally:
        memset(addressof(secret), 0, sizeof(secret))


def submit_pin(window, pin):
    fields = pin_fields(window)
    button = dialog_button(window, 'OK')
    if len(fields) != 1 or button is None:
        raise ReadError('ProfileMismatch')
    write_pin_field(fields[0], pin)
    press_button(window, button)


@dataclass
class FilingSession:
    executable: Path
    pid: int
    handle: int
    wizard: Wizard
    step: int
    invoked: set = field(default_factory=set)
    pin_steps: set = field(default_factory=set)

    def _action(self, window, key, *captions):
        if key not in self.invoked and (button := dialog_button(window, *captions)):
            self.invoked.add(key)
            press_button(window, button)
            return True
        return False

    def _modal(self, windows):
        active = [window for window in windows if window.handle != self.handle and window.is_enabled()
                  and window.element_info.class_name != 'TApplication']
        if not active:
            return None
        if len(active) != 1:
            return 'Sprawdź otwarte komunikaty w Płatniku i wznów pracę.'
        window, = active
        classname = window.element_info.class_name
        if self.wizard.mode == 'send' and classname == 'TFrmPobieranieDanychZUS' and self.step == 2:
            for caption in ('Podpisz', 'Wyślij'):
                if self._action(window, (self.step, caption), caption):
                    break
            return None
        if classname == 'TFrmDlgNPin' and self.step in self.wizard.pin_steps:
            if self.step in self.pin_steps:
                return 'Płatnik ponownie prosi o PIN. Sprawdź kartę i wpisz PIN w Płatniku.'
            pin = read_pin()
            if pin is None:
                return 'Wpisz PIN w Płatniku albo zapisz go w Biuro, a następnie wznów.'
            self.pin_steps.add(self.step)
            submit_pin(window, pin)
            return None
        return 'Przeczytaj komunikat Płatnika i wznów po jego obsłużeniu.'

    def _inline_pin(self, dialog):
        if self.step != self.wizard.pin_field_step:
            return None
        if self.step in self.pin_steps:
            return 'Sprawdź certyfikat i opcje podpisywania w Płatniku, a następnie wznów.'
        fields = pin_fields(dialog)
        if len(fields) != 1 or not dialog_button(dialog, 'Wyświetl certyfikat'):
            return 'Sprawdź certyfikat i pole PIN w kreatorze Płatnika, a następnie wznów.'
        pin = read_pin()
        if pin is None:
            return 'Wpisz PIN w Płatniku albo zapisz go w Biuro, a następnie wznów.'
        self.pin_steps.add(self.step)
        write_pin_field(fields[0], pin)
        return None

    def advance(self, *, timeout=180):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if process_image(self.pid) != self.executable:
                raise ReadError('AppUnavailable')
            windows = [window for window in desktop_windows() if window.process_id() == self.pid]
            dialog = next((window for window in windows if window.handle == self.handle), None)
            if dialog is None:
                return {'closed': True, 'finished': (self.wizard.last, 'Zakończ') in self.invoked}
            identified = identify(dialog)
            if not identified or identified[0] != self.wizard or identified[1] < self.step:
                raise ReadError('ProfileMismatch')
            self.step = identified[1]
            if not dialog.is_enabled():
                if message := self._modal(windows):
                    return {'closed': False, 'message': message}
            elif self.step == self.wizard.last:
                self._action(dialog, (self.step, 'Zakończ'), 'Zakończ')
            elif self.step in self.wizard.next_steps and (self.step, 'Dalej') not in self.invoked:
                report = self.wizard.report
                if report is None or self.step != report[0] or dialog_button(dialog, report[1]):
                    if not self._action(dialog, (self.step, 'Dalej'), *NEXT):
                        if message := self._inline_pin(dialog):
                            return {'closed': False, 'message': message}
            sleep(0.4)
        return {'closed': False, 'message': f'Kreator zatrzymał się na kroku {self.step}. Sprawdź wynik w Płatniku i wznów.'}
