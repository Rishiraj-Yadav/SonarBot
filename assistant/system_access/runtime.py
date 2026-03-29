"""Runtime helpers for host file access and PowerShell execution."""

from __future__ import annotations

import asyncio
import fnmatch
import io
import os
import re
import shutil
import zipfile
from html import escape as html_escape
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET

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
        if raw_path.startswith("~"):
            candidate = self.home_root / raw_path[1:].lstrip("\\/")
        else:
            candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.home_root / candidate
        return self._resolve_known_user_folder(candidate.resolve())

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

    def _known_user_folder_names(self) -> set[str]:
        return {"desktop", "documents", "downloads", "pictures", "music", "videos"}

    def _iter_one_drive_roots(self, base_home: Path) -> list[Path]:
        if not base_home.exists():
            return []
        roots = []
        for child in base_home.iterdir():
            if child.is_dir() and child.name.lower().startswith("onedrive"):
                roots.append(child)
        return roots

    def _iter_known_user_folder_candidates(self, base_home: Path, folder_name: str) -> list[Path]:
        candidates = [base_home / folder_name]
        for one_drive_root in self._iter_one_drive_roots(base_home):
            candidates.append(one_drive_root / folder_name)
        return candidates

    def _preferred_known_user_folder(self, folder_name: str) -> Path:
        bases: list[Path] = []
        for base in (self.home_root, Path.home().resolve()):
            resolved_base = base.resolve()
            if resolved_base not in bases:
                bases.append(resolved_base)
        first_candidate: Path | None = None
        for base in bases:
            for candidate in self._iter_known_user_folder_candidates(base, folder_name):
                if first_candidate is None:
                    first_candidate = candidate
                if candidate.exists():
                    return candidate.resolve()
        return (first_candidate or (self.home_root / folder_name)).resolve()

    def _resolve_known_user_folder(self, candidate: Path) -> Path:
        folder_name = candidate.name.lower()
        if folder_name not in self._known_user_folder_names():
            return candidate
        if candidate.exists():
            return candidate
        bases: list[Path] = []
        for base in (candidate.parent, self.home_root, Path.home().resolve()):
            resolved_base = base.resolve()
            if resolved_base not in bases:
                bases.append(resolved_base)
        for base in bases:
            for alt in self._iter_known_user_folder_candidates(base, candidate.name):
                if alt.exists():
                    return alt.resolve()
        return candidate

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

    async def read_document_text(self, path: Path, max_bytes: int = 10_000_000) -> dict[str, object]:
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".rtf"}:
            return await self.read_text(path, max_bytes=max_bytes)
        if suffix == ".docx":
            content = await asyncio.to_thread(self._extract_docx_text, path)
        elif suffix == ".pptx":
            content = await asyncio.to_thread(self._extract_pptx_text, path)
        elif suffix == ".pdf":
            content = await asyncio.to_thread(self._extract_pdf_text, path)
        else:
            raise ValueError(f"Unsupported document format: {suffix or path.suffix or path.name}")
        return {
            "path": str(path),
            "content": content,
            "bytes_read": len(content.encode("utf-8")),
            "line_count": len(content.splitlines()),
            "file_format": suffix.lstrip("."),
        }

    async def write_text(self, path: Path, content: str) -> dict[str, object]:
        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)
        return {"path": str(path), "bytes_written": len(content.encode("utf-8"))}

    async def write_content(self, path: Path, content: str) -> dict[str, object]:
        file_format = self._infer_write_format(path)

        def _write() -> int:
            path.parent.mkdir(parents=True, exist_ok=True)
            if file_format == "text":
                path.write_text(content, encoding="utf-8")
                return len(content.encode("utf-8"))
            if file_format == "pdf":
                data = self._build_simple_pdf_bytes(content)
                path.write_bytes(data)
                return len(data)
            if file_format == "docx":
                data = self._build_simple_docx_bytes(content)
                path.write_bytes(data)
                return len(data)
            if file_format == "doc":
                data = self._build_simple_rtf_bytes(content)
                path.write_bytes(data)
                return len(data)
            path.write_text(content, encoding="utf-8")
            return len(content.encode("utf-8"))

        bytes_written = await asyncio.to_thread(_write)
        return {"path": str(path), "bytes_written": bytes_written, "file_format": file_format}

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
        return [
            PathAccessRule(path=self._preferred_known_user_folder("Desktop")),
            PathAccessRule(path=self._preferred_known_user_folder("Documents")),
            PathAccessRule(path=self._preferred_known_user_folder("Downloads")),
            PathAccessRule(path=self._preferred_known_user_folder("Pictures")),
            PathAccessRule(path=self._preferred_known_user_folder("Music")),
            PathAccessRule(path=self._preferred_known_user_folder("Videos")),
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

    def _infer_write_format(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix == ".docx":
            return "docx"
        if suffix in {".doc", ".docs"}:
            return "doc"
        return "text"

    def _build_simple_pdf_bytes(self, text: str) -> bytes:
        lines = text.splitlines() or [text or ""]
        commands = ["BT", "/F1 12 Tf", "72 770 Td"]
        for index, line in enumerate(lines[:120]):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if index:
                commands.append("0 -16 Td")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", errors="replace")
        objects = [
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n",
            f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream\nendobj\n",
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for obj in objects:
            offsets.append(len(pdf))
            pdf.extend(obj)
        xref_offset = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        pdf.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        pdf.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
        return bytes(pdf)

    def _build_simple_rtf_bytes(self, text: str) -> bytes:
        escaped = (
            text.replace("\\", "\\\\")
            .replace("{", "\\{")
            .replace("}", "\\}")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\par\n")
        )
        return ("{\\rtf1\\ansi\\deff0\n" + escaped + "\n}").encode("utf-8")

    def _build_simple_docx_bytes(self, text: str) -> bytes:
        paragraphs = text.splitlines() or [text or ""]
        body = "".join(
            f"<w:p><w:r><w:t xml:space=\"preserve\">{html_escape(paragraph)}</w:t></w:r></w:p>"
            for paragraph in paragraphs
        )
        document_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            f"<w:body>{body}<w:sectPr/></w:body></w:document>"
        )
        content_types = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
            "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
            "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
            "<Override PartName=\"/word/document.xml\" "
            "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
            "</Types>"
        )
        relationships = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            "<Relationship Id=\"rId1\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
            "Target=\"word/document.xml\"/>"
            "</Relationships>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", relationships)
            archive.writestr("word/document.xml", document_xml)
        return buffer.getvalue()

    def _extract_pdf_text(self, path: Path) -> str:
        try:
            import pdfplumber  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pdfplumber is not installed.") from exc

        parts: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n\n".join(part for part in parts if part.strip())

    def _extract_docx_text(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            xml_text = self._read_zip_xml(archive, "word/document.xml")
        tree = ET.fromstring(xml_text)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []
        for paragraph in tree.findall(".//w:p", ns):
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns)).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs).strip()

    def _extract_pptx_text(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            presentation_xml = self._read_zip_xml(archive, "ppt/presentation.xml")
            rels_xml = self._read_zip_xml(archive, "ppt/_rels/presentation.xml.rels")

            rel_tree = ET.fromstring(rels_xml)
            rel_ns = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
            rel_map = {
                rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
                for rel in rel_tree.findall(".//rel:Relationship", rel_ns)
                if rel.attrib.get("Id") and rel.attrib.get("Target")
            }

            pres_tree = ET.fromstring(presentation_xml)
            pres_ns = {
                "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            }
            slide_ids = [
                item.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
                for item in pres_tree.findall(".//p:sldId", pres_ns)
            ]

            slides: list[str] = []
            for index, slide_id in enumerate(slide_ids, start=1):
                target = rel_map.get(slide_id, "")
                if not target:
                    continue
                slide_path = f"ppt/{target.lstrip('/')}"
                try:
                    slide_xml = self._read_zip_xml(archive, slide_path)
                except KeyError:
                    continue
                slide_tree = ET.fromstring(slide_xml)
                paragraphs: list[str] = []
                for paragraph in slide_tree.findall(".//a:p", pres_ns):
                    text = "".join(node.text or "" for node in paragraph.findall(".//a:t", pres_ns)).strip()
                    if text:
                        paragraphs.append(text)
                slide_text = "\n".join(paragraphs).strip()
                if slide_text:
                    slides.append(f"Slide {index}\n{slide_text}")
        return "\n\n".join(slides).strip()

    def _read_zip_xml(self, archive: zipfile.ZipFile, xml_path: str) -> str:
        with archive.open(xml_path) as handle:
            return handle.read().decode("utf-8", errors="replace")
