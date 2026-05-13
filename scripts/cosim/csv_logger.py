import csv
from pathlib import Path


class CsvLogger:
    """Incrementally writes rows to a CSV file, writing header on first flush."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rows: list[dict] = []
        self._header: list[str] | None = None

    def append(self, row: dict) -> None:
        if self._header is None:
            self._header = list(row.keys())
        self._rows.append(row)

    def flush(self) -> None:
        if not self._rows:
            return
        mode = "a" if self.path.exists() else "w"
        with open(self.path, mode, newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self._header or list(self._rows[0].keys()))
            if mode == "w":
                writer.writeheader()
            writer.writerows(self._rows)
        self._rows.clear()
