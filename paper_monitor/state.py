from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Set


class RecordState:
    def __init__(self, state_file: str = "data/pushed_ids.json") -> None:
        self.path = Path(state_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_path = self.path.with_name("pushed_dois.json")

    @staticmethod
    def _clean(x: object) -> str:
        s = str(x).strip()
        return "" if s.upper() in {"", "NULL", "NONE", "N/A", "NA"} else s

    def _read_ids_from_file(self, path: Path) -> Set[str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("ids")
        if raw is None:
            raw = data.get("dois", [])
        return {self._clean(x) for x in raw if self._clean(x)}

    def load(self) -> Set[str]:
        if self.path.exists():
            try:
                return self._read_ids_from_file(self.path)
            except Exception:
                return set()

        if self.legacy_path.exists():
            try:
                migrated = self._read_ids_from_file(self.legacy_path)
                self.save(migrated)
                return migrated
            except Exception:
                return set()

        return set()

    def save(self, ids: Iterable[str]) -> None:
        unique = sorted({self._clean(x) for x in ids if self._clean(x)})
        payload = {"ids": unique}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_many(self, new_ids: Iterable[str]) -> None:
        existing = self.load()
        existing.update({self._clean(x) for x in new_ids if self._clean(x)})
        self.save(existing)


class DOIState(RecordState):
    """Backward-compatible alias."""

    def __init__(self, state_file: str = "data/pushed_dois.json") -> None:
        translated = "data/pushed_ids.json" if state_file.endswith("pushed_dois.json") else state_file
        super().__init__(translated)

    def load(self) -> Set[str]:
        try:
            return super().load()
        except Exception:
            return set()
