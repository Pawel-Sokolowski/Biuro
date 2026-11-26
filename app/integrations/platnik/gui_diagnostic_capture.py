"""Capture structural metadata from a manual Płatnik wizard walkthrough."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from time import sleep

from app.integrations.platnik.gui_desktop import interactive_desktop, process_image
from app.integrations.platnik.gui_evidence import ReadError

POLL_SECONDS = 0.5
MAX_CONTROLS_PER_WINDOW = 500
CAPTURE_FORMAT = 'biuro-platnik-wizard-capture-v1'


def default_output_path():
    base = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'Biuro' / 'diagnostics'
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    return base / f'platnik-wizard-{stamp}.json'


def classify_window(title):
    normalized = ' '.join(title.casefold().split())
    kind = 'other'
    if 'kreator' in normalized and any(word in normalized for word in ('potwierdz', 'pobier')):
        kind = 'confirmation_wizard'
    elif 'kreator' in normalized and any(word in normalized for word in ('wysył', 'wysyl')):
        kind = 'send_wizard'
    elif 'lista zestaw' in normalized:
        kind = 'set_picker'
    elif 'płatnik' in normalized or 'platnik' in normalized:
        kind = 'platnik_window'

    step_match = re.search(r'\bkrok\s*(\d{1,2})(?:\s*/\s*(\d{1,2}))?', normalized)
    step = None
    if step_match:
        step = {'current': int(step_match[1]), 'total': int(step_match[2]) if step_match[2] else None}
    return {'kind': kind, 'step': step}


def discover_executable():
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    from pywinauto import Desktop

    candidates = set()
    images = {}
    for window in Desktop(backend='win32').windows(visible_only=True):
        pid = window.process_id()
        if pid not in images:
            images[pid] = process_image(pid)
        path = images[pid]
        if (path and path.name.casefold() in ('p2.exe', 'platnik.exe')
                and 'asseco' in str(path.parent).casefold()):
            candidates.add(path)
    if len(candidates) != 1:
        raise ReadError('AppUnavailable')
    return candidates.pop()


def safe_button_caption(raw):
    caption = ' '.join(str(raw or '').split())
    if (not caption or len(caption) > 80 or re.search(r'\d|@', caption)
            or re.search(r'\b(?:nip|pesel|pin|hasło|haslo)\b', caption, re.IGNORECASE)):
        return None
    return caption


def rectangle(control):
    try:
        rect = control.rectangle()
        return {'left': rect.left, 'top': rect.top, 'right': rect.right, 'bottom': rect.bottom}
    except Exception:
        return None


def describe_backend(handle, backend):
    from pywinauto import Desktop

    window = Desktop(backend=backend).window(handle=handle)
    controls = []
    for control in window.wrapper_object().descendants():
        info = control.element_info
        control_type = getattr(info, 'control_type', None)
        item = {
            'control_type': control_type,
            'class_name': getattr(info, 'class_name', None),
            'auto_id': getattr(info, 'automation_id', None),
            'control_id': getattr(info, 'control_id', None),
            'rectangle': rectangle(control),
        }
        if control_type in ('Button', 'CheckBox', 'RadioButton', 'TabItem'):
            caption = safe_button_caption(control.window_text())
            if caption:
                item['caption'] = caption
        controls.append(item)
        if len(controls) >= MAX_CONTROLS_PER_WINDOW:
            break
    return {'controls': controls, 'controls_truncated': len(controls) == MAX_CONTROLS_PER_WINDOW}


def describe_win32_children(handle):
    import win32gui

    controls = []
    def collect(hwnd, _):
        class_name = win32gui.GetClassName(hwnd)
        item = {
            'class_name': class_name,
            'control_id': win32gui.GetDlgCtrlID(hwnd),
            'visible': bool(win32gui.IsWindowVisible(hwnd)),
            'enabled': bool(win32gui.IsWindowEnabled(hwnd)),
        }
        try:
            rect = win32gui.GetWindowRect(hwnd)
            item['rectangle'] = {'left': rect[0], 'top': rect[1], 'right': rect[2], 'bottom': rect[3]}
        except Exception:
            item['rectangle'] = None
        if class_name == 'Button':
            caption = safe_button_caption(win32gui.GetWindowText(hwnd))
            if caption:
                item['caption'] = caption
        controls.append(item)
        return len(controls) < MAX_CONTROLS_PER_WINDOW

    win32gui.EnumChildWindows(handle, collect, None)
    return {'controls': controls, 'controls_truncated': len(controls) == MAX_CONTROLS_PER_WINDOW}


def summarize_window(specification):
    wrapper = specification
    title = wrapper.window_text()
    handle = wrapper.handle
    backends = {}
    for backend in ('win32', 'uia'):
        try:
            backends[backend] = describe_backend(handle, backend)
        except Exception as error:
            backends[backend] = {'error': type(error).__name__}
    try:
        win32_children = describe_win32_children(handle)
    except Exception as error:
        win32_children = {'error': type(error).__name__}
    return {
        **classify_window(title),
        'hwnd': handle,
        'pid': specification.process_id(),
        'class_name': wrapper.element_info.class_name,
        'visible': wrapper.is_visible(),
        'enabled': wrapper.is_enabled(),
        'rectangle': rectangle(wrapper),
        'win32_children': win32_children,
        'pywinauto': backends,
    }


def capture_windows(config):
    if not interactive_desktop():
        raise ReadError('DesktopUnavailable')
    from pywinauto import Desktop

    images = {}
    captured = []
    for window in Desktop(backend='win32').windows(visible_only=True):
        pid = window.process_id()
        if pid not in images:
            images[pid] = process_image(pid)
        if images[pid] != config['executable']:
            continue
        captured.append(summarize_window(window))
    return captured


def write_bundle(path, bundle):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def capture_wizard_walkthrough(config, output=None, *, report=print, poll_seconds=POLL_SECONDS):
    """Passively capture distinct Płatnik window layouts until Ctrl+C."""
    if sys.platform != 'win32':
        raise ValueError('Diagnostyka Płatnika wymaga Windows.')
    path = Path(output) if output is not None else default_output_path()
    path = path.expanduser().resolve()
    if path.exists():
        raise FileExistsError(f'Plik diagnostyczny już istnieje: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        'format': CAPTURE_FORMAT,
        'scope': 'UI structure and button captions only; no screenshots, titles, or field/table values.',
        'captures': [],
    }
    seen = set()
    report('Przechwytywanie aktywne. Przejdź ręcznie przez kreator; naciśnij Ctrl+C, aby zapisać plik.')
    waiting = True
    try:
        while True:
            windows = capture_windows(config)
            if windows:
                if waiting:
                    report('Znaleziono widoczne okna Płatnika.')
                    waiting = False
                signature = json.dumps(windows, ensure_ascii=False, sort_keys=True)
                if signature not in seen:
                    seen.add(signature)
                    bundle['captures'].append({
                        'captured_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                        'windows': windows,
                    })
                    write_bundle(path, bundle)
                    report(f'Zapisano stan {len(bundle["captures"])} ({len(windows)} okien Płatnika).')
            elif not waiting:
                report('Brak widocznych okien Płatnika; nadal oczekuję na kolejne kroki.')
                waiting = True
            sleep(poll_seconds)
    except KeyboardInterrupt:
        write_bundle(path, bundle)
        report(f'Plik diagnostyczny: {path} (zapisane stany: {len(bundle["captures"])}).')
    except Exception as error:
        if bundle['captures']:
            write_bundle(path, bundle)
            report(f'Zapisano częściową diagnostykę: {path} ({type(error).__name__}).')
        raise
    return path, len(bundle['captures'])
