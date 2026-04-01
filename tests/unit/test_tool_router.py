from __future__ import annotations

from assistant.ml.tool_router import ToolRouter


def _schemas(names: list[str]) -> list[dict[str, object]]:
    return [{"name": name, "description": name, "parameters": {"type": "object", "properties": {}}} for name in names]


def test_tool_router_disabled_returns_all_tools() -> None:
    router = ToolRouter(enabled=False)
    schemas = _schemas(["read_file", "write_file", "gmail_latest_email"])
    selected, decision = router.select_tools("show my latest email", schemas)
    assert len(selected) == len(schemas)
    assert decision.fallback_used is True


def test_tool_router_heuristic_selects_relevant_tools_with_safety() -> None:
    router = ToolRouter(
        enabled=True,
        shadow_mode=False,
        safety_tools=["llm_task"],
    )
    schemas = _schemas(["llm_task", "gmail_latest_email", "github_list_repos"])
    selected, decision = router.select_tools("show my latest email", schemas)
    selected_names = {str(item["name"]) for item in selected}
    assert "gmail_latest_email" in selected_names
    assert "llm_task" in selected_names
    assert decision.confidence > 0


def test_tool_router_shadow_mode_keeps_all_schemas() -> None:
    router = ToolRouter(
        enabled=True,
        shadow_mode=True,
        safety_tools=["llm_task"],
    )
    schemas = _schemas(["llm_task", "gmail_latest_email", "github_list_repos"])
    selected, _ = router.select_tools("show my latest email", schemas)
    assert len(selected) == len(schemas)

