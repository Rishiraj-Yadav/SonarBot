"""Runtime helpers for host file access and PowerShell execution."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from assistant.system_access.policy import PathAccessRule, matches_protected_path, most_specific_rule


class SystemAccessRuntime:
    def __init__(self, config, store) -> None:
        self.config = config
        self.store = store
        self.home_root = config.system_access.home_root.resolve()
        self.backup_root = config.system_access.backup_root.resolve()
        self.protected_roots = [Path(path).expanduser().resolve() for path in config.system_access.protected_roots]
        self.path_rules = self._build_effective_path_rules()
        self.default_outside_policy = config.system_access.default_outside_policy
        self.default_workdir = self._choose_default_workdir()

    def resolve_host_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.home_root / candidate
        return candidate.resolve()

    def resolve_home_path(self, raw_path: str) -> Path:
        return self.resolve_host_path(raw_path)

    def classify_path_action(self, path: Path, action: str) -> tuple[str, str]:
        resolved = path.resolve()
        if matches_protected_path(resolved, self.protected_roots):
            return "deny", "protected_path"
        rule = most_specific_rule(resolved, self.path_rules)
        if rule is None:
            return self.default_outside_policy, "outside_policy"
        return rule.category_for_action(action), f"rule:{rule.path}"

    def default_search_roots(self) -> list[Path]:
        readable: list[Path] = []
        for rule in self.path_rules:
            if rule.read == "deny":
                continue
            if any(existing == rule.path or self._is_path_inside(existing, rule.path) for existing in readable):
                continue
            readable = [existing for existing in readable if not self._is_path_inside(rule.path, existing)]
            readable.append(rule.path)
        return readable

    def extract_command_paths(self, command: str) -> list[Path]:
        pattern = re.compile(
            r'(?:"(?P<dq>(?:~|[A-Za-z]:)[\\/][^"]+)"|\'(?P<sq>(?:~|[A-Za-z]:)[\\/][^\']+)\'|(?P<plain>(?:~|[A-Za-z]:)[\\/][^\s|;&]+))'
        )
        paths: list[Path] = []
        seen: set[str] = set()
        for match in pattern.finditer(command):
            raw = match.group("dq") or match.group("sq") or match.group("plain")
            if not raw:
                continue
            try:
                resolved = self.resolve_host_path(raw)
            except Exception:
                continue
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            paths.append(resolved)
        return paths

    async def exec_command(self, command: str, *, timeout: int, workdir: str | None = None) -> dict[str, object]:
        working_directory = self.resolve_host_path(workdir or str(self.default_workdir))
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command,
            cwd=str(working_directory),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {"stdout": "", "stderr": f"Command timed out after {timeout} seconds.", "exit_code": -1}
        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
            "workdir": str(working_directory),
        }

    async def backup_file(self, path: Path, action_kind: str) -> tuple[str, Path]:
        backup_id = uuid4().hex
        backup_path = self.backup_root / backup_id / path.name

        def _copy() -> None:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)

        await asyncio.to_thread(_copy)
        await self.store.create_backup(
            backup_id,
            original_path=str(path),
            backup_path=str(backup_path),
            action_kind=action_kind,
        )
        return backup_id, backup_path

    async def read_text(self, path: Path, max_bytes: int = 250_000) -> dict[str, object]:
        stat_result = await asyncio.to_thread(path.stat)
        if stat_result.st_size > max_bytes:
            raise ValueError(f"File is too large to read as text ({stat_result.st_size} bytes).")
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return {
            "path": str(path),
            "content": content,
            "bytes_read": len(content.encode("utf-8")),
            "line_count": len(content.splitlines()),
        }

    async def write_text(self, path: Path, content: str) -> dict[str, object]:
        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)
        return {"path": str(path), "bytes_written": len(content.encode("utf-8"))}

    async def delete_path(self, path: Path) -> dict[str, object]:
        def _delete() -> None:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)
        return {"path": str(path), "deleted": True}

    async def copy_path(self, source: Path, destination: Path) -> dict[str, object]:
        def _copy() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)

        await asyncio.to_thread(_copy)
        return {"source": str(source), "destination": str(destination), "copied": True}

    async def move_path(self, source: Path, destination: Path) -> dict[str, object]:
        def _move() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))

        await asyncio.to_thread(_move)
        return {"source": str(source), "destination": str(destination), "moved": True}

    async def list_directory(self, path: Path, limit: int = 200) -> dict[str, object]:
        def _list() -> list[dict[str, object]]:
            entries = []
            for item in sorted(path.iterdir(), key=lambda candidate: candidate.name.lower())[:limit]:
                entries.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "size": item.stat().st_size if item.exists() and not item.is_dir() else 0,
                    }
                )
            return entries

        entries = await asyncio.to_thread(_list)
        return {"path": str(path), "entries": entries}

    async def search_files(
        self,
        root: Path,
        *,
        pattern: str = "*",
        text: str = "",
        name_query: str = "",
        directories_only: bool = False,
        files_only: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        normalized_text = text.lower().strip()
        normalized_name_query = name_query.lower().strip()
        normalized_compact_query = re.sub(r"[^a-z0-9]+", "", normalized_name_query)
        pattern_lower = pattern.lower().strip() or "*"
        noise_directories = {
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
            "dist",
            "build",
            ".next",
            "site-packages",
            "appdata",
        }

        def _compact(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", value.lower())

        def _matches_name(candidate_name: str) -> bool:
            lowered_name = candidate_name.lower()
            if pattern_lower not in {"", "*"} and not fnmatch.fnmatch(lowered_name, pattern_lower):
                return False
            if not normalized_name_query:
                return True
            stem = Path(candidate_name).stem.lower()
            compact_name = _compact(candidate_name)
            compact_stem = _compact(Path(candidate_name).stem)
            return (
                normalized_name_query in lowered_name
                or normalized_name_query in stem
                or (normalized_compact_query and normalized_compact_query in compact_name)
                or (normalized_compact_query and normalized_compact_query in compact_stem)
            )

        def _score_candidate(candidate: Path, *, is_dir: bool) -> int:
            name = candidate.name.lower()
            stem = candidate.stem.lower()
            compact_name = _compact(candidate.name)
            compact_stem = _compact(candidate.stem)
            score = 0
            if normalized_name_query:
                token_pattern = rf"(^|[^a-z0-9]){re.escape(normalized_name_query)}([^a-z0-9]|$)"
                if name == normalized_name_query or stem == normalized_name_query:
                    score += 160
                elif normalized_compact_query and (
                    compact_name == normalized_compact_query or compact_stem == normalized_compact_query
                ):
                    score += 150
                elif name.startswith(normalized_name_query) or stem.startswith(normalized_name_query):
                    score += 120
                elif normalized_compact_query and (
                    compact_name.startswith(normalized_compact_query) or compact_stem.startswith(normalized_compact_query)
                ):
                    score += 115
                elif re.search(token_pattern, name):
                    score += 100
                elif normalized_name_query in name or normalized_name_query in stem:
                    score += 60
                elif normalized_compact_query and (
                    normalized_compact_query in compact_name or normalized_compact_query in compact_stem
                ):
                    score += 55
            if is_dir:
                score += 35
            if directories_only and is_dir:
                score += 200
            if files_only and not is_dir:
                score += 200
            score -= min(len(candidate.parts), 20)
            return score

        def _search() -> list[dict[str, object]]:
            matches: list[dict[str, object]] = []
            root_str = str(root)
            for current_root, dirnames, filenames in os.walk(root_str, topdown=True):
                dirnames[:] = [name for name in dirnames if name.lower() not in noise_directories]
                current_path = Path(current_root)

                if not files_only:
                    for dirname in dirnames:
                        candidate = current_path / dirname
                        if not _matches_name(dirname):
                            continue
                        matches.append(
                            {
                                "path": str(candidate),
                                "name": dirname,
                                "is_dir": True,
                                "score": _score_candidate(candidate, is_dir=True),
                            }
                        )

                if directories_only:
                    continue

                for filename in filenames:
                    candidate = current_path / filename
                    if not _matches_name(filename):
                        continue
                    item = {
                        "path": str(candidate),
                        "name": filename,
                        "is_dir": False,
                        "score": _score_candidate(candidate, is_dir=False),
                    }
                    if normalized_text:
                        try:
                            content = candidate.read_text(encoding="utf-8")
                        except Exception:
                            continue
                        if normalized_text not in content.lower():
                            continue
                        item["line_count"] = len(content.splitlines())
                    matches.append(item)

            matches.sort(
                key=lambda item: (
                    -int(item.get("score", 0)),
                    len(Path(str(item.get("path", ""))).parts),
                    str(item.get("name", "")).lower(),
                )
            )
            trimmed = matches[:limit]
            for item in trimmed:
                item.pop("score", None)
            return trimmed

        matches = await asyncio.to_thread(_search)
        return {
            "root": str(root),
            "matches": matches,
            "directories_only": directories_only,
            "files_only": files_only,
            "name_query": normalized_name_query,
        }

    async def restore_backup(self, backup: dict[str, object]) -> dict[str, object]:
        original_path = self.resolve_host_path(str(backup["original_path"]))
        backup_path = Path(str(backup["backup_path"])).resolve()

        def _restore() -> None:
            original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, original_path)

        await asyncio.to_thread(_restore)
        await self.store.mark_backup_restored(str(backup["backup_id"]))
        return {"backup_id": str(backup["backup_id"]), "restored_path": str(original_path)}

    def _build_effective_path_rules(self) -> list[PathAccessRule]:
        configured = getattr(self.config.system_access, "path_rules", [])
        if configured:
            rules: list[PathAccessRule] = []
            for rule in configured:
                if isinstance(rule, dict):
                    path = Path(rule["path"]).expanduser().resolve()
                    read = rule.get("read", "auto_allow")
                    write = rule.get("write", "ask_once")
                    overwrite = rule.get("overwrite", "always_ask")
                    delete = rule.get("delete", "always_ask")
                    execute = rule.get("execute", "ask_once")
                else:
                    path = Path(getattr(rule, "path")).expanduser().resolve()
                    read = getattr(rule, "read", "auto_allow")
                    write = getattr(rule, "write", "ask_once")
                    overwrite = getattr(rule, "overwrite", "always_ask")
                    delete = getattr(rule, "delete", "always_ask")
                    execute = getattr(rule, "execute", "ask_once")
                rules.append(
                    PathAccessRule(
                        path=path,
                        read=read,
                        write=write,
                        overwrite=overwrite,
                        delete=delete,
                        execute=execute,
                    )
                )
            return rules
        if self.home_root != Path.home().resolve():
            return [
                PathAccessRule(
                    path=self.home_root,
                    read="auto_allow",
                    write="ask_once",
                    overwrite="always_ask",
                    delete="always_ask",
                    execute="ask_once",
                )
            ]
        user_home = Path.home().resolve()
        return [
            PathAccessRule(path=(user_home / "Desktop").resolve()),
            PathAccessRule(path=(user_home / "Documents").resolve()),
            PathAccessRule(path=(user_home / "Downloads").resolve()),
            PathAccessRule(path=(user_home / "Pictures").resolve()),
            PathAccessRule(path=(user_home / "Music").resolve()),
            PathAccessRule(path=(user_home / "Videos").resolve()),
            PathAccessRule(path=Path("R:/").resolve()),
        ]

    def _is_path_inside(self, root: Path, candidate: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    def _choose_default_workdir(self) -> Path:
        for rule in self.path_rules:
            if rule.execute == "deny":
                continue
            if rule.path.exists():
                return rule.path
        return self.home_root
