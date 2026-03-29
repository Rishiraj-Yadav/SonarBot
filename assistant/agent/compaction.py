"""Session compaction support."""

from __future__ import annotations

import asyncio

from assistant.agent.context import build_model_messages, estimate_model_payload_size, should_skip_from_model
from assistant.agent.session import Session
from assistant.memory.compaction_flush import MemoryFlushRunner
from assistant.agent.streaming import merge_text_chunks


class CompactionManager:
    def __init__(self, config, session_manager, model_provider, tool_registry=None) -> None:
        self.config = config
        self.session_manager = session_manager
        self.model_provider = model_provider
        self.flush_runner = MemoryFlushRunner(model_provider, tool_registry) if tool_registry is not None else None
        self.log_path = self.config.logs_dir / "compaction.log"

    async def maybe_compact(
        self,
        session: Session,
        system_prompt: str,
        *,
        aggressive: bool = False,
        force: bool = False,
    ) -> bool:
        threshold = int(self.config.agent.context_window * 0.75)
        is_automation = session.session_key.startswith("automation:")
        is_browser = bool(
            (session.metadata or {}).get("browser_task_state")
            or (session.metadata or {}).get("browser_workflow_state")
        )
        message_threshold = 24 if is_automation else (32 if is_browser else 60)
        token_threshold = min(threshold, 4096 if is_automation else (max(2048, threshold // 2) if is_browser else threshold))
        payload_size = estimate_model_payload_size(build_model_messages(session.messages), system_prompt)
        payload_threshold = int(self.config.agent.context_window * (2.0 if is_browser else 3.0))
        if not force and (
            session.token_count < token_threshold
            and len(session.messages) < message_threshold
            and payload_size < payload_threshold
        ):
            return False
        if len(session.messages) < 4:
            return False

        if self.config.agent.compaction.memory_flush.enabled and self.flush_runner is not None:
            await self.flush_runner.maybe_flush(session, system_prompt)

        trim_ratio = 0.6 if aggressive or is_automation or is_browser else 0.4
        trim_count = max(1, int(len(session.messages) * trim_ratio))
        trimmed_messages = session.messages[:trim_count]
        filtered_trimmed_messages = [message for message in trimmed_messages if not should_skip_from_model(message)]
        if filtered_trimmed_messages:
            trimmed_messages = filtered_trimmed_messages
        transcript = "\n".join(
            f"{message.get('role', 'user')}: {message.get('content', '')}" for message in trimmed_messages
        )
        prompt = (
            "Summarize this conversation history concisely, preserving key facts, decisions, and context.\n\n"
            f"{transcript}"
        )

        chunks: list[str] = []
        async for response in self.model_provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=system_prompt,
            tools=[],
            stream=False,
        ):
            if response.text:
                chunks.append(response.text)

        summary = merge_text_chunks(chunks) or transcript[:500]
        await self.session_manager.apply_compaction(session, trimmed_messages, summary)
        if self.flush_runner is not None:
            self.flush_runner.reset_cycle(session)
        await self._log_compaction(session.session_key, session.session_id, len(trimmed_messages))
        return True

    async def _log_compaction(self, session_key: str, session_id: str, trimmed_count: int) -> None:
        line = f"{session_key}\t{session_id}\ttrimmed={trimmed_count}\n"

        def _write() -> None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

        await asyncio.to_thread(_write)
