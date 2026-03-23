"""Classification rules and path-policy helpers for Windows-first host access."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from assistant.system_access.models import ApprovalCategory


DENY_PATTERNS = (
    re.compile(r"\bformat-volume\b", re.IGNORECASE),
    re.compile(r"\bclear-disk\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\b(bcdedit|bootsect|bootrec)\b", re.IGNORECASE),
    re.compile(r"\bdd\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+(/|c:\\)\b", re.IGNORECASE),
)
SYSTEM_PATH_PATTERNS = (
    re.compile(r"c:\\windows", re.IGNORECASE),
    re.compile(r"c:\\program files", re.IGNORECASE),
    re.compile(r"c:\\programdata", re.IGNORECASE),
    re.compile(r"\b(reg(\.exe)?|set-itemproperty|new-itemproperty|remove-itemproperty)\b", re.IGNORECASE),
)
AUTO_ALLOW_PREFIXES = {
    "get-childitem",
    "dir",
    "ls",
    "get-content",
    "cat",
    "type",
    "select-string",
    "rg",
    "where",
    "where.exe",
    "pwd",
    "get-location",
    "get-process",
    "ps",
    "get-date",
    "whoami",
    "hostname",
    "test-path",
}
ASK_ONCE_PREFIXES = {
    "new-item",
    "copy-item",
    "move-item",
    "set-content",
    "add-content",
    "out-file",
    "invoke-webrequest",
    "curl",
    "wget",
    "python",
    "python.exe",
    "pip",
    "pip.exe",
    "npm",
    "start",
    "start-process",
    "explorer",
    "notepad",
}
ALWAYS_ASK_PREFIXES = {
    "remove-item",
    "del",
    "erase",
    "rmdir",
    "rd",
    "taskkill",
    "stop-process",
    "stop-service",
    "restart-service",
    "set-itemproperty",
    "new-itemproperty",
    "remove-itemproperty",
    "reg",
    "sc",
}
DELETE_PREFIXES = {"remove-item", "del", "erase", "rmdir", "rd"}
WRITE_PREFIXES = {"new-item", "set-content", "add-content", "out-file", "copy-item"}
MOVE_PREFIXES = {"move-item"}
EXECUTE_PREFIXES = {
    "start",
    "start-process",
    "explorer",
    "notepad",
    "python",
    "python.exe",
    "pip",
    "pip.exe",
    "npm",
}
PROTECTED_DRIVE_ROOT_NAMES = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
}
PROTECTED_ANYWHERE_NAMES = {
    "$recycle.bin",
    "system volume information",
}
CATEGORY_ORDER: dict[ApprovalCategory, int] = {
    "auto_allow": 0,
    "ask_once": 1,
    "always_ask": 2,
    "deny": 3,
}


@dataclass(frozen=True, slots=True)
class PathAccessRule:
    path: Path
    read: ApprovalCategory = "auto_allow"
    write: ApprovalCategory = "ask_once"
    overwrite: ApprovalCategory = "always_ask"
    delete: ApprovalCategory = "always_ask"
    execute: ApprovalCategory = "ask_once"

    def category_for_action(self, action: str) -> ApprovalCategory:
        if action == "read":
            return self.read
        if action == "write":
            return self.write
        if action == "overwrite":
            return self.overwrite
        if action == "delete":
            return self.delete
        if action == "execute":
            return self.execute
        return self.write


def split_command_segments(command: str) -> list[str]:
    parts = re.split(r"(?:\|\||&&|[|;])", command)
    return [part.strip() for part in parts if part.strip()]


def classify_command(command: str) -> tuple[ApprovalCategory, str]:
    highest: ApprovalCategory = "auto_allow"
    reason = "read_only"
    for segment in split_command_segments(command):
        category, item_reason = classify_command_segment(segment)
        if category == "deny":
            return category, item_reason
        highest = max_category(highest, category)
        if highest == category:
            reason = item_reason
    return highest, reason


def classify_command_segment(segment: str) -> tuple[ApprovalCategory, str]:
    normalized = segment.strip()
    lowered = normalized.lower()
    for pattern in DENY_PATTERNS:
        if pattern.search(lowered):
            return "deny", "destructive_operation"
    for pattern in SYSTEM_PATH_PATTERNS:
        if pattern.search(lowered):
            return "always_ask", "system_path_or_registry"
    if any(operator in lowered for operator in (" >", ">>", "2>", "| out-file")):
        return "ask_once", "redirection"
    token = command_prefix(lowered)
    if token in ALWAYS_ASK_PREFIXES:
        return "always_ask", "destructive_or_system_command"
    if token in AUTO_ALLOW_PREFIXES:
        return "auto_allow", "read_only"
    if token in ASK_ONCE_PREFIXES:
        return "ask_once", "generic_host_execution"
    return "ask_once", "generic_host_execution"


def command_prefix(command: str) -> str:
    parts = command.split(None, 1)
    return parts[0].lower() if parts else ""


def infer_command_path_action(command: str, command_category: ApprovalCategory) -> str:
    token = command_prefix(command.lower())
    lowered = command.lower()
    if token in DELETE_PREFIXES:
        return "delete"
    if token in MOVE_PREFIXES:
        return "overwrite"
    if token in WRITE_PREFIXES or any(operator in lowered for operator in (" >", ">>", "2>", "| out-file")):
        return "write"
    if token in EXECUTE_PREFIXES:
        return "execute"
    if command_category == "auto_allow":
        return "read"
    if command_category == "always_ask":
        return "delete"
    return "write"


def max_category(*categories: ApprovalCategory) -> ApprovalCategory:
    return max(categories, key=lambda item: CATEGORY_ORDER[item], default="auto_allow")


def is_path_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def matches_protected_path(candidate: Path, protected_roots: Iterable[Path]) -> bool:
    resolved = candidate.resolve()
    for root in protected_roots:
        if is_path_inside(root.resolve(), resolved) or resolved == root.resolve():
            return True
    parts = [part.lower() for part in resolved.parts]
    if any(name in parts for name in PROTECTED_ANYWHERE_NAMES):
        return True
    if len(parts) >= 2 and re.match(r"^[a-z]:\\?$", parts[0], flags=re.IGNORECASE):
        first_component = parts[1]
        if first_component in PROTECTED_DRIVE_ROOT_NAMES:
            return True
    return False


def most_specific_rule(candidate: Path, rules: Iterable[PathAccessRule]) -> PathAccessRule | None:
    resolved = candidate.resolve()
    matched: list[tuple[int, PathAccessRule]] = []
    for rule in rules:
        rule_path = rule.path.resolve()
        if resolved == rule_path or is_path_inside(rule_path, resolved):
            matched.append((len(rule_path.parts), rule))
    if not matched:
        return None
    matched.sort(key=lambda item: item[0], reverse=True)
    return matched[0][1]

