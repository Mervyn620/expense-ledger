import json
import os
import threading
from pathlib import Path


class AnalysisCache:
    def __init__(self, path, max_entries=12):
        self.path = Path(path)
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            payload = self._read()
            return payload.get("entries", {}).get(key)

    def put(self, key, value):
        with self._lock:
            payload = self._read()
            entries = payload.setdefault("entries", {})
            entries[key] = value
            if len(entries) > self.max_entries:
                ordered = sorted(
                    entries.items(),
                    key=lambda item: item[1].get("generated_at", ""),
                    reverse=True,
                )
                payload["entries"] = dict(ordered[: self.max_entries])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "entries": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") == 1 and isinstance(payload.get("entries"), dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "entries": {}}
