"""Lightweight NLP intent classifier for natural-language routing."""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from typing import Any

import httpx

from assistant.gateway.router import (
    APP_CONTROL_FOCUS_PATTERN,
    APP_CONTROL_MAXIMIZE_PATTERN,
    APP_CONTROL_MINIMIZE_PATTERN,
    APP_CONTROL_OPEN_PATTERN,
    APP_CONTROL_RESTORE_PATTERN,
    APP_CONTROL_SNAP_PATTERN,
    APP_SKILL_BRIGHTNESS_SET_PATTERN,
    APP_SKILL_SETTINGS_PATTERN,
    APP_SKILL_VOLUME_SET_PATTERN,
    DESKTOP_INPUT_CLICK_PATTERN,
    DESKTOP_INPUT_CLIPBOARD_WRITE_PATTERN,
    DESKTOP_INPUT_DOUBLE_CLICK_PATTERN,
    DESKTOP_INPUT_MOVE_PATTERN,
    DESKTOP_INPUT_PRESS_PATTERN,
    DESKTOP_INPUT_RIGHT_CLICK_PATTERN,
    DESKTOP_INPUT_SCROLL_PATTERN,
    DESKTOP_INPUT_TYPE_PATTERN,
    DESKTOP_VISION_ACTIVE_PHRASES,
    DESKTOP_VISION_CAPTURE_PHRASES,
    DESKTOP_VISION_READ_DESKTOP_PHRASES,
    DESKTOP_VISION_READ_WINDOW_PHRASES,
    DESKTOP_VISION_WINDOW_PHRASES,
    NATURAL_LANGUAGE_CRON_PATTERNS,
    ONE_TIME_REMINDER_PATTERNS,
)


class IntentClassifier:
    VALID_INTENTS = {
        "open_app",
        "browser_task",
        "schedule_report",
        "schedule_reminder",
        "desktop_control",
        "file_op",
        "report_generate",
        "chat",
        "coworker_task",
        "screen_action",
        "clipboard_action",
        "unknown",
    }
    COMMON_APP_ALIASES = {
        "chrome",
        "edge",
        "vscode",
        "notepad",
        "explorer",
        "word",
        "excel",
        "whatsapp",
        "taskmanager",
        "task manager",
        "settings",
        "calculator",
        "paint",
        "cmd",
        "powershell",
        "outlook",
    }
    INTENT_KEYWORDS = {
        "open",
        "launch",
        "start",
        "go",
        "browser",
        "click",
        "take",
        "screenshot",
        "screen",
        "read",
        "remind",
        "every",
        "today",
        "tomorrow",
        "report",
        "file",
        "folder",
        "desktop",
        "clipboard",
        "copy",
        "paste",
        "type",
        "scroll",
        "press",
        "bluetooth",
        "volume",
        "brightness",
        "app",
        "window",
        "control",
        "coworker",
    }
    BUILTIN_WORDS = {
        "a",
        "an",
        "and",
        "app",
        "at",
        "browser",
        "button",
        "capture",
        "check",
        "chrome",
        "click",
        "clipboard",
        "close",
        "control",
        "copy",
        "daily",
        "desktop",
        "display",
        "document",
        "down",
        "edge",
        "email",
        "emails",
        "excel",
        "explorer",
        "file",
        "folder",
        "focus",
        "for",
        "generate",
        "gmail",
        "go",
        "hotkey",
        "in",
        "increase",
        "launch",
        "maximize",
        "minimize",
        "my",
        "navigate",
        "notepad",
        "of",
        "off",
        "on",
        "open",
        "paste",
        "press",
        "read",
        "remind",
        "report",
        "restore",
        "right",
        "screen",
        "screenshot",
        "scroll",
        "set",
        "settings",
        "show",
        "site",
        "start",
        "summary",
        "switch",
        "tab",
        "take",
        "task",
        "text",
        "the",
        "to",
        "toggle",
        "turn",
        "up",
        "visible",
        "volume",
        "vscode",
        "weather",
        "what",
        "when",
        "which",
        "window",
        "with",
        "word",
        "write",
    } | COMMON_APP_ALIASES

    def __init__(self, config) -> None:
        self.config = config
        self._cache: dict[str, dict] = {}

    async def classify(self, message: str) -> dict:
        key = message.lower().strip()
        if key in self._cache:
            return dict(self._cache[key])

        prechecked = await self._regex_precheck(message)
        if prechecked["confidence"] >= 0.85:
            self._remember(key, prechecked)
            return dict(prechecked)

        llm_result: dict[str, Any] | None = None
        if getattr(self.config.llm, "gemini_api_key", ""):
            try:
                llm_result = await self._classify_with_llm(message)
            except Exception:
                llm_result = None
        if llm_result is None:
            llm_result = self._fallback_classification(message, prechecked)
        normalized = self._normalize_result(llm_result, original=message)
        if normalized["intent"] == "unknown" and prechecked["intent"] != "unknown":
            normalized = dict(prechecked)
        self._remember(key, normalized)
        return dict(normalized)

    async def fuzzy_match_app(self, user_input: str, known_aliases: set[str]) -> str | None:
        candidate = re.sub(r"\s+", " ", str(user_input).strip().lower())
        if not candidate:
            return None
        aliases = {alias.strip().lower() for alias in known_aliases if alias and alias.strip()}
        aliases.update(self.COMMON_APP_ALIASES)
        if candidate in aliases:
            return candidate
        close = difflib.get_close_matches(candidate, sorted(aliases), n=1, cutoff=0.6)
        if close:
            return close[0]
        best_alias: str | None = None
        best_score = 0.0
        for alias in aliases:
            distance = self._levenshtein_distance(candidate, alias)
            scale = max(len(candidate), len(alias), 1)
            score = 1.0 - (distance / scale)
            if score > best_score:
                best_score = score
                best_alias = alias
        if best_alias is not None and best_score >= 0.6:
            return best_alias
        return None

    async def rewrite_canonical(self, message: str) -> str:
        original = str(message).strip()
        if not original:
            return original
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9._:/-]*", original.lower())
        if len(tokens) < 3:
            return original
        if not self._has_intent_keyword(tokens):
            return original
        if not self._looks_like_needing_rewrite(tokens):
            return original
        if not getattr(self.config.llm, "gemini_api_key", ""):
            return original
        prompt = (
            "Rewrite this command in clean English, fix spelling, keep meaning exactly. "
            f"Return only the rewritten sentence, nothing else: {original}"
        )
        try:
            rewritten = await self._request_text_completion(
                system_prompt="You rewrite Windows desktop commands faithfully.",
                user_prompt=prompt,
            )
        except Exception:
            return original
        cleaned = rewritten.strip()
        return cleaned or original

    async def _regex_precheck(self, message: str) -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", str(message).strip())
        lowered = normalized.lower()
        result = self._empty_result(message)
        if not lowered:
            return result

        generic_reminder_match = re.match(r"^remind me at (?P<time>.+?) to (?P<message>.+)$", lowered, flags=re.IGNORECASE)
        if generic_reminder_match is not None:
            result.update(
                {
                    "intent": "schedule_reminder",
                    "target": str(generic_reminder_match.group("message")).strip(),
                    "action": "remind",
                    "time_expr": str(generic_reminder_match.group("time")).strip(),
                    "confidence": 0.9,
                    "raw_slots": dict(generic_reminder_match.groupdict()),
                }
            )
            return result

        for pattern in ONE_TIME_REMINDER_PATTERNS:
            match = re.match(pattern, lowered, flags=re.IGNORECASE)
            if match is None:
                continue
            result.update(
                {
                    "intent": "schedule_reminder",
                    "target": str(match.group("message")).strip(),
                    "action": "remind",
                    "time_expr": f"{match.group('day')} at {match.group('time')}",
                    "confidence": 0.95,
                    "raw_slots": dict(match.groupdict()),
                }
            )
            return result

        for pattern in NATURAL_LANGUAGE_CRON_PATTERNS:
            match = re.match(pattern, lowered, flags=re.IGNORECASE)
            if match is None:
                continue
            reminder_text = str(match.groupdict().get("message", "")).strip()
            result.update(
                {
                    "intent": "schedule_reminder",
                    "target": reminder_text,
                    "action": "schedule",
                    "time_expr": str(match.groupdict().get("time") or match.groupdict().get("time_of_day") or "").strip(),
                    "confidence": 0.94,
                    "raw_slots": dict(match.groupdict()),
                }
            )
            return result

        known_aliases = self._known_aliases()
        for action_name, pattern in (
            ("open", APP_CONTROL_OPEN_PATTERN),
            ("focus", APP_CONTROL_FOCUS_PATTERN),
            ("minimize", APP_CONTROL_MINIMIZE_PATTERN),
            ("maximize", APP_CONTROL_MAXIMIZE_PATTERN),
            ("restore", APP_CONTROL_RESTORE_PATTERN),
        ):
            match = re.match(pattern, lowered)
            if match is None:
                continue
            raw_target = self._clean_target(str(match.group("target")))
            corrected_target = await self.fuzzy_match_app(raw_target, known_aliases)
            if corrected_target is None:
                corrected_target = raw_target if raw_target in known_aliases else raw_target
            corrected = self._rewrite_target_sentence(normalized, raw_target, corrected_target)
            result.update(
                {
                    "intent": "open_app" if action_name == "open" else "desktop_control",
                    "target": corrected_target,
                    "action": action_name,
                    "corrected": corrected,
                    "confidence": 0.9 if corrected_target in known_aliases else 0.72,
                    "raw_slots": {"target": corrected_target},
                }
            )
            return result

        snap_match = re.match(APP_CONTROL_SNAP_PATTERN, lowered)
        if snap_match is not None:
            raw_target = self._clean_target(str(snap_match.group("target")))
            corrected_target = await self.fuzzy_match_app(raw_target, known_aliases)
            result.update(
                {
                    "intent": "desktop_control",
                    "target": corrected_target or raw_target,
                    "action": "snap",
                    "confidence": 0.9 if corrected_target or raw_target in known_aliases else 0.74,
                    "raw_slots": {
                        "target": corrected_target or raw_target,
                        "position": str(snap_match.group("position")).lower(),
                    },
                }
            )
            return result

        if lowered in DESKTOP_VISION_ACTIVE_PHRASES:
            result.update({"intent": "screen_action", "action": "active", "confidence": 0.96})
            return result
        if lowered in DESKTOP_VISION_CAPTURE_PHRASES or lowered in {"take a screenshot", "capture a screenshot", "capture screen"}:
            result.update({"intent": "screen_action", "action": "capture", "confidence": 0.94})
            return result
        if lowered in DESKTOP_VISION_WINDOW_PHRASES:
            result.update({"intent": "screen_action", "action": "window", "confidence": 0.95})
            return result
        if lowered in DESKTOP_VISION_READ_DESKTOP_PHRASES or lowered in DESKTOP_VISION_READ_WINDOW_PHRASES:
            result.update({"intent": "screen_action", "action": "read", "confidence": 0.95})
            return result

        if lowered == "copy selected text" or lowered in {"what is on my clipboard", "what's on my clipboard", "read my clipboard", "get clipboard"}:
            result.update({"intent": "clipboard_action", "action": "read", "confidence": 0.95})
            return result
        if re.match(DESKTOP_INPUT_CLIPBOARD_WRITE_PATTERN, lowered):
            result.update({"intent": "clipboard_action", "action": "write", "confidence": 0.93})
            return result
        for action_name, pattern in (
            ("move", DESKTOP_INPUT_MOVE_PATTERN),
            ("click", DESKTOP_INPUT_CLICK_PATTERN),
            ("double_click", DESKTOP_INPUT_DOUBLE_CLICK_PATTERN),
            ("right_click", DESKTOP_INPUT_RIGHT_CLICK_PATTERN),
            ("scroll", DESKTOP_INPUT_SCROLL_PATTERN),
            ("type", DESKTOP_INPUT_TYPE_PATTERN),
            ("hotkey", DESKTOP_INPUT_PRESS_PATTERN),
        ):
            if re.match(pattern, lowered):
                intent_name = "screen_action" if action_name in {"click", "double_click", "right_click", "move", "scroll", "type", "hotkey"} else "clipboard_action"
                result.update({"intent": intent_name, "action": action_name, "confidence": 0.91})
                return result

        if re.search(r"\b(?:visible|on screen|on the screen|highlighted|click this|open that one)\b", lowered):
            result.update({"intent": "coworker_task", "action": "visual", "confidence": 0.9})
            return result

        if re.search(r"\b(?:https?://|www\.|gmail\.com|github\.com|google\.com|leetcode\.com)\b", lowered) or (
            any(token in lowered for token in ("go to", "browse", "website", "open site"))
            and any(token in lowered for token in ("mail", "email", "browser", ".com"))
        ):
            url_match = re.search(r"(https?://\S+|www\.\S+|[a-z0-9.-]+\.[a-z]{2,})(?:\b|/)", lowered)
            result.update(
                {
                    "intent": "browser_task",
                    "target": url_match.group(1) if url_match else "",
                    "action": "browse",
                    "confidence": 0.92,
                    "raw_slots": {"url": url_match.group(1) if url_match else ""},
                }
            )
            return result

        if re.search(r"\b(?:generate|create|make|prepare|build)\b.+\b(?:report|summary|briefing)\b", lowered):
            result.update({"intent": "report_generate", "target": "report", "action": "generate", "confidence": 0.9})
            return result

        if re.search(r"\b(?:open|read|write|create|update|delete|rename|copy|move)\b.+\b(?:file|folder|document|txt|pdf|docx|xlsx)\b", lowered) or re.search(
            r"\b[a-z]:[\\/]", lowered
        ):
            result.update({"intent": "file_op", "action": "file_op", "confidence": 0.82})
            return result

        if re.search(r"\b(?:weather|time|date|who|what|why|how)\b", lowered):
            result.update({"intent": "chat", "action": "answer", "confidence": 0.88})
            return result

        return result

    def _fallback_classification(self, message: str, prechecked: dict[str, Any]) -> dict[str, Any]:
        if prechecked["intent"] != "unknown":
            return prechecked
        lowered = re.sub(r"\s+", " ", message.strip().lower())
        result = self._empty_result(message)
        if lowered.endswith("?") or lowered.startswith(("what ", "who ", "why ", "how ", "when ")):
            result.update({"intent": "chat", "action": "answer", "confidence": 0.55})
        return result

    async def _classify_with_llm(self, message: str) -> dict[str, Any]:
        system_prompt = (
            "You are a strict JSON command parser for a Windows desktop AI assistant.\n"
            "Never add explanation. Return only raw JSON, no markdown fences."
        )
        user_prompt = (
            "Parse this user message and return JSON with these exact keys:\n"
            "intent, target, action, time_expr, corrected, confidence, raw_slots.\n\n"
            "intent must be one of: open_app, browser_task, schedule_report,\n"
            "schedule_reminder, desktop_control, file_op, report_generate, chat,\n"
            "coworker_task, screen_action, clipboard_action, unknown\n\n"
            "confidence is a float 0.0-1.0 representing how sure you are.\n"
            "corrected is the spell-fixed version of the input (same as input if no errors).\n"
            "raw_slots is a dict of any other useful fields extracted from the message.\n\n"
            f'Message: "{message}"'
        )
        raw_text = await self._request_text_completion(system_prompt=system_prompt, user_prompt=user_prompt)
        return self._parse_json_payload(raw_text)

    async def _request_text_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        }
        if system_prompt:
            payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
        model_names = self._candidate_models()
        async with httpx.AsyncClient(timeout=30.0) as client:
            data: dict[str, Any] | None = None
            last_error: Exception | None = None
            for index, model_name in enumerate(model_names):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                response = await client.post(url, params={"key": self.config.llm.gemini_api_key}, json=payload)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code not in {400, 404} or index == len(model_names) - 1:
                        raise
                    continue
                data = response.json()
                break
        if data is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Gemini classifier could not reach a compatible model.")
        chunks: list[str] = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    chunks.append(str(part["text"]))
        return "\n".join(chunks).strip()

    def _candidate_models(self) -> list[str]:
        candidates = ["gemini-2.0-flash", str(self.config.agent.model)]
        unique: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            unique.append(normalized)
            seen.add(normalized)
        return unique

    def _parse_json_payload(self, raw_text: str) -> dict[str, Any]:
        candidate = raw_text.strip()
        fenced = re.search(r"\{[\s\S]*\}", candidate)
        if fenced is not None:
            candidate = fenced.group(0)
        return json.loads(candidate)

    def _normalize_result(self, result: dict[str, Any], *, original: str) -> dict[str, Any]:
        intent = str(result.get("intent", "unknown")).strip().lower()
        if intent not in self.VALID_INTENTS:
            intent = "unknown"
        corrected = str(result.get("corrected", "")).strip() or original
        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        raw_slots = result.get("raw_slots", {})
        if not isinstance(raw_slots, dict):
            raw_slots = {}
        return {
            "intent": intent,
            "target": str(result.get("target", "")).strip(),
            "action": str(result.get("action", "")).strip(),
            "time_expr": str(result.get("time_expr", "")).strip(),
            "corrected": corrected,
            "confidence": confidence,
            "raw_slots": raw_slots,
        }

    def _empty_result(self, message: str) -> dict[str, Any]:
        return {
            "intent": "unknown",
            "target": "",
            "action": "",
            "time_expr": "",
            "corrected": str(message).strip(),
            "confidence": 0.0,
            "raw_slots": {},
        }

    def _remember(self, key: str, value: dict[str, Any]) -> None:
        self._cache[key] = dict(value)
        if len(self._cache) > 500:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)

    def _known_aliases(self) -> set[str]:
        configured = getattr(getattr(self.config, "desktop_apps", None), "known_apps", {}) or {}
        aliases = {str(alias).strip().lower() for alias in configured.keys() if str(alias).strip()}
        aliases.update(self.COMMON_APP_ALIASES)
        return aliases

    def _clean_target(self, value: str) -> str:
        target = value.strip().strip("\"'")
        target = re.sub(r"^(?:the|my)\s+", "", target)
        target = re.sub(r"\s+app$", "", target)
        target = re.sub(r"\s+window$", "", target)
        return target.strip().lower()

    def _rewrite_target_sentence(self, message: str, original_target: str, corrected_target: str) -> str:
        if not original_target or not corrected_target:
            return message
        return re.sub(re.escape(original_target), corrected_target, message, count=1, flags=re.IGNORECASE)

    def _has_intent_keyword(self, tokens: list[str]) -> bool:
        for token in tokens:
            if token in self.INTENT_KEYWORDS:
                return True
            if difflib.get_close_matches(token, sorted(self.INTENT_KEYWORDS), n=1, cutoff=0.8):
                return True
        return False

    def _looks_like_needing_rewrite(self, tokens: list[str]) -> bool:
        noisy_tokens = 0
        for token in tokens:
            if self._looks_structured_token(token):
                continue
            if token in self.BUILTIN_WORDS:
                continue
            if difflib.get_close_matches(token, sorted(self.BUILTIN_WORDS), n=1, cutoff=0.8):
                noisy_tokens += 1
                continue
            if len(token) > 3 and re.search(r"[aeiou]", token) is None:
                noisy_tokens += 1
        return noisy_tokens > 0

    def _looks_structured_token(self, token: str) -> bool:
        return bool(
            re.match(r"^\d", token)
            or re.match(r"^[a-z]:[/\\]", token)
            or "." in token
            or ":" in token
            or "/" in token
        )

    def _levenshtein_distance(self, left: str, right: str) -> int:
        if left == right:
            return 0
        if not left:
            return len(right)
        if not right:
            return len(left)
        previous = list(range(len(right) + 1))
        for index, left_char in enumerate(left, start=1):
            current = [index]
            for right_index, right_char in enumerate(right, start=1):
                insert_cost = current[right_index - 1] + 1
                delete_cost = previous[right_index] + 1
                replace_cost = previous[right_index - 1] + (0 if left_char == right_char else 1)
                current.append(min(insert_cost, delete_cost, replace_cost))
            previous = current
        return previous[-1]
