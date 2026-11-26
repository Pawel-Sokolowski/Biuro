from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

import psutil
import win32gui
import win32process
from pywinauto import Desktop


OUTPUT_DIR = Path(__file__).resolve().parent / "platnik_ui_dump"


def safe(value):
    try:
        return str(value)
    except Exception:
        return "<ERROR>"


def get_process_info(pid: int) -> dict:
    try:
        p = psutil.Process(pid)

        try:
            exe = p.exe()
        except Exception:
            exe = None

        try:
            name = p.name()
        except Exception:
            name = None

        return {
            "pid": pid,
            "name": name,
            "exe": exe,
        }

    except Exception as exc:
        return {
            "pid": pid,
            "name": None,
            "exe": None,
            "error": str(exc),
        }


def enum_top_windows() -> list[dict]:
    windows = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindow(hwnd):
                return True

            title = win32gui.GetWindowText(hwnd)
            class_name = win32gui.GetClassName(hwnd)

            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            rect = None
            try:
                rect = win32gui.GetWindowRect(hwnd)
            except Exception:
                pass

            process = get_process_info(pid)

            windows.append(
                {
                    "hwnd": hwnd,
                    "hwnd_hex": hex(hwnd),
                    "pid": pid,
                    "title": title,
                    "class_name": class_name,
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    "enabled": bool(win32gui.IsWindowEnabled(hwnd)),
                    "rect": rect,
                    "process_name": process.get("name"),
                    "exe": process.get("exe"),
                }
            )

        except Exception:
            pass

        return True

    win32gui.EnumWindows(callback, None)

    return windows


def is_probably_platnik(window: dict) -> bool:
    parts = [
        window.get("title"),
        window.get("class_name"),
        window.get("process_name"),
        window.get("exe"),
    ]

    text = " ".join(str(x or "") for x in parts).lower()

    keywords = [
        "platnik",
        "płatnik",
        "asseco",
    ]

    return any(word in text for word in keywords)


def print_window(window: dict):
    print("=" * 100)
    print(f"HWND:       {window['hwnd_hex']}")
    print(f"PID:        {window['pid']}")
    print(f"PROCESS:    {window['process_name']}")
    print(f"EXE:        {window['exe']}")
    print(f"TITLE:      {window['title']!r}")
    print(f"CLASS:      {window['class_name']!r}")
    print(f"VISIBLE:    {window['visible']}")
    print(f"ENABLED:    {window['enabled']}")
    print(f"RECT:       {window['rect']}")


def dump_win32_children(hwnd: int, output_file: Path):
    rows = []

    def callback(child_hwnd, _):
        try:
            _, pid = win32process.GetWindowThreadProcessId(child_hwnd)

            try:
                rect = win32gui.GetWindowRect(child_hwnd)
            except Exception:
                rect = None

            try:
                control_id = win32gui.GetDlgCtrlID(child_hwnd)
            except Exception:
                control_id = None

            rows.append(
                {
                    "hwnd": child_hwnd,
                    "hwnd_hex": hex(child_hwnd),
                    "pid": pid,
                    "title": win32gui.GetWindowText(child_hwnd),
                    "class_name": win32gui.GetClassName(child_hwnd),
                    "control_id": control_id,
                    "visible": bool(win32gui.IsWindowVisible(child_hwnd)),
                    "enabled": bool(win32gui.IsWindowEnabled(child_hwnd)),
                    "rect": rect,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "hwnd": child_hwnd,
                    "error": str(exc),
                }
            )

        return True

    try:
        win32gui.EnumChildWindows(hwnd, callback, None)
    except Exception as exc:
        rows.append({"fatal_error": str(exc)})

    with output_file.open("w", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            f.write(f"--- CONTROL {i} ---\n")

            for key, value in row.items():
                f.write(f"{key}: {value}\n")

            f.write("\n")

    return rows


def dump_pywinauto_tree(hwnd: int, backend: str, output_file: Path):
    with output_file.open("w", encoding="utf-8") as f:
        old_stdout = sys.stdout

        try:
            sys.stdout = f

            print(f"BACKEND: {backend}")
            print(f"HWND: {hex(hwnd)}")
            print("=" * 100)
            print()

            desktop = Desktop(backend=backend)
            window = desktop.window(handle=hwnd)

            print("WINDOW:")
            print(window)

            try:
                print()
                print("ELEMENT INFO:")
                print(window.element_info)
            except Exception:
                traceback.print_exc()

            print()
            print("=" * 100)
            print("CONTROL IDENTIFIERS")
            print("=" * 100)

            window.print_control_identifiers()

        except Exception:
            traceback.print_exc()

        finally:
            sys.stdout = old_stdout


def dump_uia_descendants(hwnd: int, output_file: Path):
    data = []

    try:
        desktop = Desktop(backend="uia")
        window = desktop.window(handle=hwnd)

        root = window.wrapper_object()

        descendants = root.descendants()

        for index, control in enumerate(descendants):
            try:
                info = control.element_info

                rect = None

                try:
                    r = info.rectangle

                    rect = {
                        "left": r.left,
                        "top": r.top,
                        "right": r.right,
                        "bottom": r.bottom,
                    }
                except Exception:
                    pass

                row = {
                    "index": index,
                    "name": safe(getattr(info, "name", None)),
                    "control_type": safe(
                        getattr(info, "control_type", None)
                    ),
                    "class_name": safe(
                        getattr(info, "class_name", None)
                    ),
                    "automation_id": safe(
                        getattr(info, "automation_id", None)
                    ),
                    "framework_id": safe(
                        getattr(info, "framework_id", None)
                    ),
                    "handle": safe(
                        getattr(info, "handle", None)
                    ),
                    "rectangle": rect,
                }

                data.append(row)

            except Exception as exc:
                data.append(
                    {
                        "index": index,
                        "error": str(exc),
                    }
                )

    except Exception as exc:
        data.append(
            {
                "fatal_error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return data


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 100)
    print("PŁATNIK UI DIAGNOSTIC")
    print("=" * 100)

    print("Timestamp:", datetime.now().isoformat())
    print("Python:", sys.version)
    print("Executable:", sys.executable)
    print("Windows:", platform.platform())
    print(
        "Administrator:",
        bool(ctypes.windll.shell32.IsUserAnAdmin()),
    )

    print()
    print("Szukam okien Płatnika...")

    all_windows = enum_top_windows()

    with (OUTPUT_DIR / "all_top_windows.json").open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            all_windows,
            f,
            ensure_ascii=False,
            indent=2,
        )

    platnik_windows = [
        w for w in all_windows
        if is_probably_platnik(w)
    ]

    if not platnik_windows:
        print()
        print("NIE ZNALAZŁEM AUTOMATYCZNIE OKNA PŁATNIKA.")
        print()
        print(
            "Zapisałem jednak wszystkie okna systemu do:"
        )
        print(
            OUTPUT_DIR / "all_top_windows.json"
        )
        print()
        print(
            "Uruchom Płatnika, zostaw widoczne główne okno "
            "i uruchom skrypt ponownie."
        )
        return

    print()
    print(
        f"Znaleziono pasujących okien: {len(platnik_windows)}"
    )
    print()

    for index, window in enumerate(platnik_windows, 1):
        print_window(window)

        hwnd = window["hwnd"]

        prefix = (
            f"{index:02d}_"
            f"pid_{window['pid']}_"
            f"hwnd_{hwnd}"
        )

        print()
        print("[1/3] Win32 EnumChildWindows...")

        win32_controls = dump_win32_children(
            hwnd,
            OUTPUT_DIR / f"{prefix}_win32_children.txt",
        )

        print(
            f"      Win32 wykrył kontrolek: "
            f"{len(win32_controls)}"
        )

        print("[2/3] pywinauto WIN32...")

        dump_pywinauto_tree(
            hwnd,
            "win32",
            OUTPUT_DIR / f"{prefix}_pywinauto_win32.txt",
        )

        print("[3/3] UI Automation...")

        uia_controls = dump_uia_descendants(
            hwnd,
            OUTPUT_DIR / f"{prefix}_uia.json",
        )

        print(
            f"      UIA wykrył elementów: "
            f"{len(uia_controls)}"
        )

        dump_pywinauto_tree(
            hwnd,
            "uia",
            OUTPUT_DIR / f"{prefix}_pywinauto_uia.txt",
        )

        print()

    print("=" * 100)
    print("GOTOWE")
    print("=" * 100)

    print()
    print("Wyniki znajdują się w:")
    print(OUTPUT_DIR)
    print()

    print("Najważniejsze pliki:")

    for file in sorted(OUTPUT_DIR.iterdir()):
        print(" -", file.name)

    print()
    print(
        "Podeślij mi zawartość *_win32_children.txt, "
        "*_pywinauto_win32.txt, *_uia.json oraz "
        "*_pywinauto_uia.txt."
    )


if __name__ == "__main__":
    main()