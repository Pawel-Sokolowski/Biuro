"""Private, bounded-memory spool of validated native CSV/XLSX rows."""

from pathlib import Path
import pickle
import tempfile
from typing import Any, BinaryIO, Iterator


def fingerprint(path: str) -> tuple[int, int, int, int, int]:
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class PreparedCompanyImport:
    def __init__(self, path: str):
        self.path = str(Path(path).resolve())
        self.signature = fingerprint(path)
        self.headers: list[str] = []
        self.file: BinaryIO = tempfile.TemporaryFile(mode="w+b")

    def append(self, row: Any) -> None:
        # Only this process writes the spool; never unpickle uploaded files.
        pickle.dump(row, self.file, protocol=pickle.HIGHEST_PROTOCOL)

    def validate(self, path: str) -> None:
        if (
            str(Path(path).resolve()) != self.path
            or fingerprint(path) != self.signature
        ):
            raise ValueError(
                "Plik zmienił się po sprawdzeniu. Wybierz go ponownie i sprawdź import."
            )

    def rows(self, path: str) -> Iterator[Any]:
        self.validate(path)
        self.file.seek(0)
        while True:
            try:
                row = pickle.load(self.file)
            except EOFError:
                return
            yield row

    def close(self) -> None:
        self.file.close()

    def __del__(self):
        if hasattr(self, "file"):
            self.close()
