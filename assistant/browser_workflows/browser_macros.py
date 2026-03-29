"""Named browser macro shortcuts — stored persistently in workspace/browser_macros.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_MACROS_FILENAME = "browser_macros.json"


class BrowserMacroStore:
    """A simple JSON-backed store for named browser command shortcuts.

    Macros are stored at ``<workspace_dir>/browser_macros.json`` as a flat
    dict mapping alias → command string.

    Example::

        store = BrowserMacroStore(workspace_dir)
        store.save("lofi", "open youtube and play lofi hip hop radio")
        command = store.resolve("lofi")
        # → "open youtube and play lofi hip hop radio"
    """

    def __init__(self, workspace_dir: Path | str) -> None:
        self._path = Path(workspace_dir) / _MACROS_FILENAME

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, alias: str, command: str) -> None:
        """Save or overwrite a macro alias."""
        alias = alias.strip().lower()
        command = command.strip()
        if not alias or not command:
            raise ValueError("Macro alias and command must be non-empty strings.")
        macros = self._load()
        macros[alias] = command
        self._save(macros)

    def resolve(self, alias: str) -> str | None:
        """Return the command string for *alias*, or None if not found."""
        return self._load().get(alias.strip().lower())

    def delete(self, alias: str) -> bool:
        """Delete a macro by alias. Returns True if it existed."""
        alias = alias.strip().lower()
        macros = self._load()
        if alias not in macros:
            return False
        del macros[alias]
        self._save(macros)
        return True

    def list_macros(self) -> dict[str, str]:
        """Return a copy of all saved macros as {alias: command}."""
        return dict(self._load())

    # ------------------------------------------------------------------
    # Convenience aliases (used by router.py slash commands)
    # ------------------------------------------------------------------

    def save_macro(self, alias: str, command: str) -> None:
        """Alias for :meth:`save`."""
        return self.save(alias, command)

    def get_macro(self, alias: str) -> str | None:
        """Alias for :meth:`resolve`."""
        return self.resolve(alias)

    def delete_macro(self, alias: str) -> bool:
        """Alias for :meth:`delete`."""
        return self.delete(alias)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): str(v) for k, v in raw.items()}
        except Exception:
            pass
        return {}

    def _save(self, macros: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(macros, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
