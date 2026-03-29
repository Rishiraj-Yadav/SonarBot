"""High-level orchestration for Windows-first host access."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from assistant.system_access.approvals import HostApprovalManager
from assistant.system_access.audit import SystemAccessAuditLogger
from assistant.system_access.models import HostAuditEntry
from assistant.system_access.policy import classify_command, infer_command_path_action, max_category
from assistant.system_access.runtime import SystemAccessRuntime
from assistant.system_access.store import SystemAccessStore


class SystemAccessManager:
    def __init__(self, config, connection_manager=None, user_profiles=None) -> None:
        self.config = config
        self.connection_manager = connection_manager
        self.user_profiles = user_profiles
        self.store = SystemAccessStore(config)
        self.audit = SystemAccessAuditLogger(config)
        self.runtime = SystemAccessRuntime(config, self.store)
        self.approvals = HostApprovalManager(
            config,
            self.store,
            on_created=self._notify_approval_created,
            on_updated=self._notify_approval_updated,
        )

    async def initialize(self) -> None:
        await self.store.initialize()

    async def run_host_command(
        self,
        *,
        command: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
        timeout: int = 30,
        workdir: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        command_category, reason = classify_command(command)
        if command_category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="exec_shell",
                action_kind="exec_shell",
                target=command,
                category=command_category,
                approval_mode="blocked",
                outcome=f"blocked:{reason}",
                details={"stderr": "Command blocked: destructive operation.", "exit_code": 1, "host": True},
            )
        category, policy_error = self._determine_command_approval_category(command, workdir=workdir)
        if policy_error is not None:
            blocked_path, blocked_reason = policy_error
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="exec_shell",
                action_kind="exec_shell",
                target=command,
                category="deny",
                approval_mode="blocked",
                outcome=f"blocked:{blocked_reason}",
                details={
                    "stderr": f"Command references a blocked path: {blocked_path}",
                    "exit_code": 1,
                    "host": True,
                },
            )

        approval_mode = "auto"
        if category in {"ask_once", "always_ask"}:
            decision, approval_mode, approval = await self.approvals.request(
                user_id=user_id,
                session_id=session_id,
                session_key=session_key,
                connection_id=connection_id,
                channel_name=channel_name,
                action_kind="exec_shell",
                target_summary=command,
                category=category,
                payload={"command": command, "workdir": workdir or str(self.runtime.default_workdir)},
            )
            if decision != "approved":
                return await self._record_blocked_action(
                    user_id=user_id,
                    session_id=session_id,
                    tool="exec_shell",
                    action_kind="exec_shell",
                    target=command,
                    category=category,
                    approval_mode=approval_mode,
                    outcome=decision,
                    details={
                        "stderr": f"Host command {decision}.",
                        "exit_code": 1,
                        "approval_id": approval.get("approval_id"),
                        "host": True,
                    },
                )

        started = time.perf_counter()
        try:
            result = await self.runtime.exec_command(command, timeout=timeout, workdir=workdir)
        except PermissionError as exc:
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="exec_shell",
                action_kind="exec_shell",
                target=command,
                category="deny",
                approval_mode=approval_mode,
                outcome="blocked:outside_policy",
                details={"stderr": str(exc), "exit_code": 1, "host": True},
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="exec_shell",
            action_kind="exec_shell",
            target=command,
            category=category,
            approval_mode=approval_mode,
            outcome="completed" if int(result.get("exit_code", 1)) == 0 else "failed",
            exit_code=int(result.get("exit_code", 1)),
            duration_ms=duration_ms,
            details={"workdir": result.get("workdir", str(self.runtime.default_workdir))},
        )
        await self.audit.append(audit_entry)
        return {
            **result,
            "host": True,
            "status": audit_entry.outcome,
            "approval_category": category,
            "approval_mode": approval_mode,
            "audit_id": audit_entry.audit_id,
        }

    async def set_windows_monitor_brightness(
        self,
        percent: int,
        *,
        session_id: str,
        user_id: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Set built-in panel brightness via WMI (no user-controlled shell; percent clamped)."""
        self._ensure_enabled()
        pct = max(0, min(100, int(percent)))
        # CIM instances from Get-CimInstance do not expose .WmiSetBrightness(); use Invoke-CimMethod.
        command = (
            "$ErrorActionPreference='Stop'; "
            "$m = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods | Select-Object -First 1; "
            "if ($null -eq $m) { throw 'No WmiMonitorBrightnessMethods instance.' }; "
            f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{{ Timeout = 1; Brightness = {pct} }}"
        )
        started = time.perf_counter()
        try:
            result = await self.runtime.exec_command(command, timeout=timeout, workdir=None)
        except PermissionError as exc:
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="set_windows_brightness",
                action_kind="set_windows_brightness",
                target=f"brightness={pct}",
                category="deny",
                approval_mode="blocked",
                outcome="blocked:outside_policy",
                details={"stderr": str(exc), "exit_code": 1, "host": True},
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        exit_code = int(result.get("exit_code", 1))
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="set_windows_brightness",
            action_kind="set_windows_brightness",
            target=f"brightness={pct}",
            category="auto_allow",
            approval_mode="auto",
            outcome="completed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            duration_ms=duration_ms,
            details={"workdir": result.get("workdir", str(self.runtime.default_workdir))},
        )
        await self.audit.append(audit_entry)
        return {
            **{k: v for k, v in result.items() if k in ("stdout", "stderr", "exit_code", "workdir")},
            "host": True,
            "brightness_percent": pct,
            "status": audit_entry.outcome,
            "approval_category": "auto_allow",
            "approval_mode": "auto",
            "audit_id": audit_entry.audit_id,
        }

    async def open_ms_settings_default_apps(
        self,
        *,
        session_id: str,
        user_id: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Open Windows Settings on Default apps (user can pick default browser)."""
        self._ensure_enabled()
        command = (
            "$ErrorActionPreference='Stop'; "
            "Start-Process explorer.exe -ArgumentList 'ms-settings:defaultapps'"
        )
        started = time.perf_counter()
        try:
            result = await self.runtime.exec_command(command, timeout=timeout, workdir=None)
        except PermissionError as exc:
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="open_default_apps_settings",
                action_kind="open_default_apps_settings",
                target="ms-settings:defaultapps",
                category="deny",
                approval_mode="blocked",
                outcome="blocked:outside_policy",
                details={"stderr": str(exc), "exit_code": 1, "host": True},
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        exit_code = int(result.get("exit_code", 1))
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="open_default_apps_settings",
            action_kind="open_default_apps_settings",
            target="ms-settings:defaultapps",
            category="auto_allow",
            approval_mode="auto",
            outcome="completed" if exit_code == 0 else "failed",
            exit_code=exit_code,
            duration_ms=duration_ms,
            details={"workdir": result.get("workdir", str(self.runtime.default_workdir))},
        )
        await self.audit.append(audit_entry)
        return {
            **{k: v for k, v in result.items() if k in ("stdout", "stderr", "exit_code", "workdir")},
            "host": True,
            "status": audit_entry.outcome,
            "approval_category": "auto_allow",
            "approval_mode": "auto",
            "audit_id": audit_entry.audit_id,
        }

    async def read_host_file(self, *, path: str, session_id: str, user_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        resolved = self.runtime.resolve_host_path(path)
        category, reason = self.runtime.classify_path_action(resolved, "read")
        if category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="read_host_file",
                action_kind="read_host_file",
                target=str(resolved),
                category=category,
                approval_mode="blocked",
                outcome=f"blocked:{reason}",
                details={"stderr": f"Reading is not allowed for {resolved}", "exit_code": 1},
            )
        started = time.perf_counter()
        result = await self.runtime.read_text(resolved)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="read_host_file",
            action_kind="read_host_file",
            target=str(resolved),
            category="auto_allow",
            approval_mode="auto",
            outcome="completed",
            duration_ms=duration_ms,
            details={"bytes_read": result.get("bytes_read", 0)},
        )
        await self.audit.append(audit_entry)
        return {**result, "audit_id": audit_entry.audit_id}

    async def list_host_dir(self, *, path: str, session_id: str, user_id: str, limit: int = 200) -> dict[str, Any]:
        self._ensure_enabled()
        resolved = self.runtime.resolve_host_path(path)
        category, reason = self.runtime.classify_path_action(resolved, "read")
        if category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="list_host_dir",
                action_kind="list_host_dir",
                target=str(resolved),
                category=category,
                approval_mode="blocked",
                outcome=f"blocked:{reason}",
                details={"stderr": f"Listing is not allowed for {resolved}", "exit_code": 1},
            )
        started = time.perf_counter()
        result = await self.runtime.list_directory(resolved, limit=limit)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="list_host_dir",
            action_kind="list_host_dir",
            target=str(resolved),
            category="auto_allow",
            approval_mode="auto",
            outcome="completed",
            duration_ms=duration_ms,
            details={"entry_count": len(result.get("entries", []))},
        )
        await self.audit.append(audit_entry)
        return {**result, "audit_id": audit_entry.audit_id}

    async def search_host_files(
        self,
        *,
        root: str,
        pattern: str,
        text: str,
        name_query: str = "",
        directories_only: bool = False,
        files_only: bool = False,
        limit: int,
        session_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        search_roots = self._resolve_search_roots(root)
        for resolved in search_roots:
            category, reason = self.runtime.classify_path_action(resolved, "read")
            if category == "deny":
                return await self._record_blocked_action(
                    user_id=user_id,
                    session_id=session_id,
                    tool="search_host_files",
                    action_kind="search_host_files",
                    target=str(resolved),
                    category=category,
                    approval_mode="blocked",
                    outcome=f"blocked:{reason}",
                    details={"stderr": f"Searching is not allowed for {resolved}", "exit_code": 1},
                )
        started = time.perf_counter()
        combined_matches: list[dict[str, Any]] = []
        for resolved in search_roots:
            partial = await self.runtime.search_files(
                resolved,
                pattern=pattern,
                text=text,
                name_query=name_query,
                directories_only=directories_only,
                files_only=files_only,
                limit=limit,
            )
            combined_matches.extend(partial.get("matches", []))
        normalized_name = name_query.lower().strip()
        normalized_compact = re.sub(r"[^a-z0-9]+", "", normalized_name)

        def _match_rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
            name = str(item.get("name", "")).lower()
            stem = Path(name).stem
            compact_name = re.sub(r"[^a-z0-9]+", "", name)
            compact_stem = re.sub(r"[^a-z0-9]+", "", stem)
            exact_rank = 3
            if normalized_name:
                if name == normalized_name or stem == normalized_name:
                    exact_rank = 0
                elif normalized_compact and (compact_name == normalized_compact or compact_stem == normalized_compact):
                    exact_rank = 0
                elif name.startswith(normalized_name) or stem.startswith(normalized_name):
                    exact_rank = 1
                elif normalized_compact and (
                    compact_name.startswith(normalized_compact) or compact_stem.startswith(normalized_compact)
                ):
                    exact_rank = 1
                elif normalized_name in name or normalized_name in stem:
                    exact_rank = 2
                elif normalized_compact and (
                    normalized_compact in compact_name or normalized_compact in compact_stem
                ):
                    exact_rank = 2
            directory_rank = 0 if item.get("is_dir") else 1
            path_depth = len(Path(str(item.get("path", ""))).parts)
            return (exact_rank, directory_rank, path_depth, name)

        combined_matches.sort(key=_match_rank)
        seen_paths: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in combined_matches:
            path_key = str(item.get("path", "")).lower()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        result = {
            "root": str(search_roots[0]) if len(search_roots) == 1 else "@allowed",
            "matches": deduped,
            "directories_only": directories_only,
            "files_only": files_only,
            "name_query": name_query.lower().strip(),
            "searched_roots": [str(path) for path in search_roots],
        }
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="search_host_files",
            action_kind="search_host_files",
            target=result["root"],
            category="auto_allow",
            approval_mode="auto",
            outcome="completed",
            duration_ms=duration_ms,
            details={"match_count": len(result.get("matches", [])), "pattern": pattern},
        )
        await self.audit.append(audit_entry)
        return {**result, "audit_id": audit_entry.audit_id}

    async def write_host_file(
        self,
        *,
        path: str,
        content: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved = self.runtime.resolve_host_path(path)
        exists = resolved.exists()
        category, reason = self.runtime.classify_path_action(resolved, "overwrite" if exists else "write")
        if category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="write_host_file",
                action_kind="write_host_file",
                target=str(resolved),
                category=category,
                approval_mode="blocked",
                outcome=f"blocked:{reason}",
                details={"stderr": f"Writing is not allowed for {resolved}", "exit_code": 1, "bytes_written": 0},
            )
        approval_mode = "auto"
        backup_id = None
        if category in {"ask_once", "always_ask"}:
            decision, approval_mode, approval = await self.approvals.request(
                user_id=user_id,
                session_id=session_id,
                session_key=session_key,
                connection_id=connection_id,
                channel_name=channel_name,
                action_kind="write_host_file",
                target_summary=str(resolved),
                category=category,
                payload={"path": str(resolved), "overwrite": exists, "bytes": len(content.encode("utf-8"))},
            )
            if decision != "approved":
                return await self._record_blocked_action(
                    user_id=user_id,
                    session_id=session_id,
                    tool="write_host_file",
                    action_kind="write_host_file",
                    target=str(resolved),
                    category=category,
                    approval_mode=approval_mode,
                    outcome=decision,
                    details={"approval_id": approval.get("approval_id"), "bytes_written": 0},
                )
        if exists and resolved.is_file():
            backup_id, _ = await self.runtime.backup_file(resolved, "write_host_file")
        started = time.perf_counter()
        result = await self.runtime.write_content(resolved, content)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="write_host_file",
            action_kind="write_host_file",
            target=str(resolved),
            category=category,
            approval_mode=approval_mode,
            outcome="completed",
            duration_ms=duration_ms,
            backup_id=backup_id,
            details={"bytes_written": result.get("bytes_written", 0)},
        )
        await self.audit.append(audit_entry)
        return {
            **result,
            "approval_category": category,
            "approval_mode": approval_mode,
            "audit_id": audit_entry.audit_id,
            "backup_id": backup_id,
            "file_format": result.get("file_format", "text"),
        }

    async def copy_host_file(
        self,
        *,
        source: str,
        destination: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        source_path = self.runtime.resolve_host_path(source)
        destination_path = self.runtime.resolve_host_path(destination)
        overwrite = destination_path.exists()
        source_category, source_reason = self.runtime.classify_path_action(source_path, "read")
        destination_category, destination_reason = self.runtime.classify_path_action(destination_path, "overwrite" if overwrite else "write")
        if source_category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="copy_host_file",
                action_kind="copy_host_file",
                target=f"{source_path} -> {destination_path}",
                category=source_category,
                approval_mode="blocked",
                outcome=f"blocked:{source_reason}",
                details={"stderr": f"Reading is not allowed for {source_path}", "exit_code": 1},
            )
        if destination_category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="copy_host_file",
                action_kind="copy_host_file",
                target=f"{source_path} -> {destination_path}",
                category=destination_category,
                approval_mode="blocked",
                outcome=f"blocked:{destination_reason}",
                details={"stderr": f"Writing is not allowed for {destination_path}", "exit_code": 1},
            )
        category = max_category(source_category, destination_category)
        approval_mode = "auto"
        backup_id = None
        if category in {"ask_once", "always_ask"}:
            decision, approval_mode, approval = await self.approvals.request(
                user_id=user_id,
                session_id=session_id,
                session_key=session_key,
                connection_id=connection_id,
                channel_name=channel_name,
                action_kind="copy_host_file",
                target_summary=f"{source_path} -> {destination_path}",
                category=category,
                payload={"source": str(source_path), "destination": str(destination_path), "overwrite": overwrite},
            )
            if decision != "approved":
                return await self._record_blocked_action(
                    user_id=user_id,
                    session_id=session_id,
                    tool="copy_host_file",
                    action_kind="copy_host_file",
                    target=f"{source_path} -> {destination_path}",
                    category=category,
                    approval_mode=approval_mode,
                    outcome=decision,
                    details={"approval_id": approval.get("approval_id")},
                )
        if overwrite and destination_path.is_file():
            backup_id, _ = await self.runtime.backup_file(destination_path, "copy_host_file")
        started = time.perf_counter()
        result = await self.runtime.copy_path(source_path, destination_path)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="copy_host_file",
            action_kind="copy_host_file",
            target=f"{source_path} -> {destination_path}",
            category=category,
            approval_mode=approval_mode,
            outcome="completed",
            duration_ms=duration_ms,
            backup_id=backup_id,
        )
        await self.audit.append(audit_entry)
        return {
            **result,
            "approval_category": category,
            "approval_mode": approval_mode,
            "audit_id": audit_entry.audit_id,
            "backup_id": backup_id,
        }

    async def move_host_file(
        self,
        *,
        source: str,
        destination: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        source_path = self.runtime.resolve_host_path(source)
        destination_path = self.runtime.resolve_host_path(destination)
        overwrite = destination_path.exists()
        source_category, source_reason = self.runtime.classify_path_action(source_path, "write")
        destination_category, destination_reason = self.runtime.classify_path_action(destination_path, "overwrite" if overwrite else "write")
        if source_category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="move_host_file",
                action_kind="move_host_file",
                target=f"{source_path} -> {destination_path}",
                category=source_category,
                approval_mode="blocked",
                outcome=f"blocked:{source_reason}",
                details={"stderr": f"Moving is not allowed for {source_path}", "exit_code": 1},
            )
        if destination_category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="move_host_file",
                action_kind="move_host_file",
                target=f"{source_path} -> {destination_path}",
                category=destination_category,
                approval_mode="blocked",
                outcome=f"blocked:{destination_reason}",
                details={"stderr": f"Writing is not allowed for {destination_path}", "exit_code": 1},
            )
        category = max_category(source_category, destination_category)
        approval_mode = "auto"
        backup_id = None
        if category in {"ask_once", "always_ask"}:
            decision, approval_mode, approval = await self.approvals.request(
                user_id=user_id,
                session_id=session_id,
                session_key=session_key,
                connection_id=connection_id,
                channel_name=channel_name,
                action_kind="move_host_file",
                target_summary=f"{source_path} -> {destination_path}",
                category=category,
                payload={"source": str(source_path), "destination": str(destination_path), "overwrite": overwrite},
            )
            if decision != "approved":
                return await self._record_blocked_action(
                    user_id=user_id,
                    session_id=session_id,
                    tool="move_host_file",
                    action_kind="move_host_file",
                    target=f"{source_path} -> {destination_path}",
                    category=category,
                    approval_mode=approval_mode,
                    outcome=decision,
                    details={"approval_id": approval.get("approval_id")},
                )
        if overwrite and destination_path.is_file():
            backup_id, _ = await self.runtime.backup_file(destination_path, "move_host_file")
        started = time.perf_counter()
        result = await self.runtime.move_path(source_path, destination_path)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="move_host_file",
            action_kind="move_host_file",
            target=f"{source_path} -> {destination_path}",
            category=category,
            approval_mode=approval_mode,
            outcome="completed",
            duration_ms=duration_ms,
            backup_id=backup_id,
        )
        await self.audit.append(audit_entry)
        return {
            **result,
            "approval_category": category,
            "approval_mode": approval_mode,
            "audit_id": audit_entry.audit_id,
            "backup_id": backup_id,
        }

    async def delete_host_file(
        self,
        *,
        path: str,
        session_key: str,
        session_id: str,
        user_id: str,
        connection_id: str = "",
        channel_name: str = "",
    ) -> dict[str, Any]:
        self._ensure_enabled()
        resolved = self.runtime.resolve_host_path(path)
        category, reason = self.runtime.classify_path_action(resolved, "delete")
        if category == "deny":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="delete_host_file",
                action_kind="delete_host_file",
                target=str(resolved),
                category=category,
                approval_mode="blocked",
                outcome=f"blocked:{reason}",
                details={"stderr": f"Deleting is not allowed for {resolved}", "exit_code": 1},
            )
        decision, approval_mode, approval = await self.approvals.request(
            user_id=user_id,
            session_id=session_id,
            session_key=session_key,
            connection_id=connection_id,
            channel_name=channel_name,
            action_kind="delete_host_file",
            target_summary=str(resolved),
            category=category,
            payload={"path": str(resolved)},
        )
        if decision != "approved":
            return await self._record_blocked_action(
                user_id=user_id,
                session_id=session_id,
                tool="delete_host_file",
                action_kind="delete_host_file",
                target=str(resolved),
                category=category,
                approval_mode=approval_mode,
                outcome=decision,
                details={"approval_id": approval.get("approval_id")},
            )
        backup_id = None
        if resolved.exists() and resolved.is_file():
            backup_id, _ = await self.runtime.backup_file(resolved, "delete_host_file")
        started = time.perf_counter()
        result = await self.runtime.delete_path(resolved)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="delete_host_file",
            action_kind="delete_host_file",
            target=str(resolved),
            category=category,
            approval_mode=approval_mode,
            outcome="completed",
            duration_ms=duration_ms,
            backup_id=backup_id,
        )
        await self.audit.append(audit_entry)
        return {
            **result,
            "approval_category": category,
            "approval_mode": approval_mode,
            "audit_id": audit_entry.audit_id,
            "backup_id": backup_id,
        }

    async def list_approvals(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await self.approvals.list_approvals(user_id, limit=limit)

    async def decide_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        return await self.approvals.decide(approval_id, decision)

    async def list_audit(self, *, session_id: str | None = None, today_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        return await self.audit.list_entries(session_id=session_id, today_only=today_only, limit=limit)

    async def restore_backup(self, backup_id: str, *, user_id: str, session_id: str = "audit") -> dict[str, Any]:
        self._ensure_enabled()
        backup = await self.store.get_backup(backup_id)
        if backup is None:
            raise KeyError(f"Unknown backup '{backup_id}'.")
        started = time.perf_counter()
        result = await self.runtime.restore_backup(backup)
        duration_ms = int((time.perf_counter() - started) * 1000)
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool="audit_restore",
            action_kind="restore_backup",
            target=str(backup["original_path"]),
            category="always_ask",
            approval_mode="explicit",
            outcome="completed",
            duration_ms=duration_ms,
            backup_id=backup_id,
        )
        await self.audit.append(audit_entry)
        return {**result, "audit_id": audit_entry.audit_id}

    def redact_tool_result(self, tool_name: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "exec_shell" and payload.get("host"):
            return {
                "host": True,
                "status": result.get("status", "completed"),
                "approval_category": result.get("approval_category"),
                "approval_mode": result.get("approval_mode"),
                "audit_id": result.get("audit_id"),
                "exit_code": result.get("exit_code"),
                "stdout_lines": len(str(result.get("stdout", "")).splitlines()) if result.get("stdout") else 0,
                "stderr_lines": len(str(result.get("stderr", "")).splitlines()) if result.get("stderr") else 0,
                "workdir": result.get("workdir"),
            }
        if tool_name == "set_windows_brightness":
            return {
                "host": True,
                "status": result.get("status", "completed"),
                "brightness_percent": result.get("brightness_percent"),
                "approval_category": result.get("approval_category"),
                "approval_mode": result.get("approval_mode"),
                "audit_id": result.get("audit_id"),
                "exit_code": result.get("exit_code"),
                "stderr_lines": len(str(result.get("stderr", "")).splitlines()) if result.get("stderr") else 0,
            }
        if tool_name == "read_host_file":
            content = str(result.get("content", ""))
            return {
                "path": result.get("path"),
                "bytes_read": result.get("bytes_read", len(content.encode("utf-8"))),
                "line_count": result.get("line_count", len(content.splitlines())),
                "audit_id": result.get("audit_id"),
            }
        if tool_name in {"write_host_file", "copy_host_file", "move_host_file", "delete_host_file"}:
            return {key: value for key, value in result.items() if key != "content"}
        if tool_name == "list_host_dir":
            entries = result.get("entries", [])
            return {"path": result.get("path"), "entry_count": len(entries), "audit_id": result.get("audit_id")}
        if tool_name == "search_host_files":
            matches = result.get("matches", [])
            return {"root": result.get("root"), "match_count": len(matches), "audit_id": result.get("audit_id")}
        return result

    def _ensure_enabled(self) -> None:
        if not self.config.system_access.enabled:
            raise RuntimeError("System access is disabled in config.toml.")

    async def _record_blocked_action(
        self,
        *,
        user_id: str,
        session_id: str,
        tool: str,
        action_kind: str,
        target: str,
        category: str,
        approval_mode: str,
        outcome: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        audit_entry = HostAuditEntry(
            user_id=user_id,
            session_id=session_id,
            tool=tool,
            action_kind=action_kind,
            target=target,
            category=category,
            approval_mode=approval_mode,
            outcome=outcome,
            duration_ms=0,
            exit_code=details.get("exit_code"),
            details={key: value for key, value in details.items() if key != "exit_code"},
        )
        await self.audit.append(audit_entry)
        return {
            **details,
            "status": outcome,
            "approval_category": category,
            "approval_mode": approval_mode,
            "audit_id": audit_entry.audit_id,
            "host": True,
        }

    async def _notify_approval_created(self, approval: dict[str, Any]) -> None:
        if self.connection_manager is not None:
            await self.connection_manager.send_user_event(
                approval["user_id"],
                "host_approval.created",
                approval,
                channel_name="webchat",
            )
        await self._send_telegram_approval(approval)

    async def _notify_approval_updated(self, approval: dict[str, Any]) -> None:
        if self.connection_manager is not None:
            await self.connection_manager.send_user_event(
                approval["user_id"],
                "host_approval.updated",
                approval,
                channel_name="webchat",
            )
            telegram_channel = self.connection_manager.get_channel("telegram")
            if telegram_channel is not None and hasattr(telegram_channel, "finalize_host_approval"):
                await telegram_channel.finalize_host_approval(approval["approval_id"], approval)

    async def _send_telegram_approval(self, approval: dict[str, Any]) -> None:
        if self.connection_manager is None or self.user_profiles is None:
            return
        telegram_channel = self.connection_manager.get_channel("telegram")
        if telegram_channel is None or not hasattr(telegram_channel, "send_host_approval_request"):
            return
        identity = await self.user_profiles.get_identity(str(approval["user_id"]), "telegram")
        if identity is None:
            return
        recipient_id = str(identity["metadata"].get("chat_id") or identity["identity_value"])
        await telegram_channel.send_host_approval_request(recipient_id, approval)

    def _determine_command_approval_category(
        self,
        command: str,
        *,
        workdir: str | None = None,
    ) -> tuple[str, tuple[str, str] | None]:
        command_category, _ = classify_command(command)
        final_category = command_category
        path_action = infer_command_path_action(command, command_category)

        if workdir:
            workdir_path = self.runtime.resolve_host_path(workdir)
            workdir_category, workdir_reason = self.runtime.classify_path_action(workdir_path, "execute")
            if workdir_category == "deny":
                return "deny", (str(workdir_path), workdir_reason)
            final_category = max_category(final_category, workdir_category)

        for path in self.runtime.extract_command_paths(command):
            category, reason = self.runtime.classify_path_action(path, path_action)
            if category == "deny":
                return "deny", (str(path), reason)
            final_category = max_category(final_category, category)
        return final_category, None

    def _resolve_search_roots(self, root: str) -> list[Path]:
        normalized = (root or "").strip()
        if normalized in {"", "*", "@allowed"}:
            return self.runtime.default_search_roots()
        return [self.runtime.resolve_host_path(normalized)]
