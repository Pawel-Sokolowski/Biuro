"""Uruchamia kolejno wszystkie pliki testowe z katalogu tests i zapisuje wyniki do logu.

Użycie:  python run_tests.py            (wszystkie testy)
         python run_tests.py -v         (pełne wyjście testów w logu i na konsoli)
         python run_tests.py calendar   (tylko pliki, których nazwa zawiera "calendar")

Log trafia do katalogu logs/tests/ (plik z datą oraz test_results_latest.log).
Kod wyjścia: 0 gdy wszystkie testy przeszły, 1 gdy którykolwiek zawiódł.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"
LOG_DIR = ROOT / "logs" / "tests"
MODULE_TIMEOUT_S = 180


def discover(patterns: list[str]) -> list[str]:
    modules = sorted(p.stem for p in TESTS_DIR.glob("test_*.py"))
    if patterns:
        modules = [m for m in modules if any(p in m for p in patterns)]
    return modules


def run_module(module: str, verbose: bool) -> tuple[int, str, float]:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", f"tests.{module}", "-v"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=MODULE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 1, f"{partial}\nPrzekroczono limit czasu {MODULE_TIMEOUT_S}s — test zawisł.", time.perf_counter() - started
    return proc.returncode, proc.stdout + proc.stderr, time.perf_counter() - started


def count_tests(output: str) -> str:
    match = re.search(r"Ran (\d+) tests?", output)
    return match.group(1) if match else "?"


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    patterns = [a for a in argv if not a.startswith("-")]
    modules = discover(patterns)
    if not modules:
        print("Nie znaleziono żadnych plików testowych.")
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = LOG_DIR / f"test_results_{stamp}.log"
    latest_path = LOG_DIR / "test_results_latest.log"

    lines: list[str] = [f"Uruchomienie testów: {datetime.now():%Y-%m-%d %H:%M:%S}",
                        f"Python: {sys.executable}", f"Pliki testowe: {len(modules)}", "=" * 70]
    summary: list[tuple[str, str, str, float]] = []

    for module in modules:
        print(f"[{len(summary) + 1}/{len(modules)}] {module} ...", end=" ", flush=True)
        code, output, seconds = run_module(module, verbose)
        status = "OK" if code == 0 else "BŁĄD"
        summary.append((module, status, count_tests(output), seconds))
        print(f"{status} ({count_tests(output)} testów, {seconds:.2f}s)")
        lines += [f"\n### {module} — {status} ({seconds:.2f}s)", "-" * 70, output.rstrip()]
        if verbose or code != 0:
            print(output.rstrip())

    failed = [s for s in summary if s[1] != "OK"]
    lines += ["", "=" * 70, "PODSUMOWANIE"]
    for module, status, count, seconds in summary:
        lines.append(f"{status:5} {module:35} testów: {count:>3}  czas: {seconds:.2f}s")
    lines.append(f"Razem: {len(summary)} plików, niepowodzenia: {len(failed)}")
    text = "\n".join(lines) + "\n"
    log_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    print("-" * 60)
    print(f"Razem: {len(summary)} plików, niepowodzenia: {len(failed)}")
    print(f"Log: {log_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
