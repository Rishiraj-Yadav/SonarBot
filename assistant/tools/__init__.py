"""Tool registry factory."""

from assistant.tools.acp_tool import build_acp_tools
from assistant.tools.agent_send_tool import build_agent_send_tool
from assistant.tools.browser_tool import build_browser_tools
from assistant.tools.exec_tool import build_exec_tool
from assistant.tools.file_tool import build_file_tools
from assistant.tools.github_tool import build_github_tools
from assistant.tools.gmail_tool import build_gmail_tools
from assistant.tools.host_file_tool import build_host_file_tools
from assistant.tools.llm_task_tool import build_llm_task_tool
from assistant.tools.memory_tool import build_memory_tools
from assistant.tools.oauth_tool import build_oauth_tools
from assistant.tools.pdf_tool import build_pdf_tools
from assistant.tools.registry import ToolRegistry
from assistant.tools.search_tool import build_search_tools


def create_default_tool_registry(
    config,
    memory_manager=None,
    model_provider=None,
    oauth_flow_manager=None,
    oauth_token_manager=None,
    sub_agent_manager=None,
    sandbox_runtime=None,
    acp_client=None,
    system_access_manager=None,
    browser_event_emitter=None,
    browser_viewer_checker=None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_file_tools(config.agent.workspace_dir):
        registry.register(tool)
    registry.register(
        build_exec_tool(
            config.agent.workspace_dir,
            sandbox_runtime=sandbox_runtime,
            sandbox_enabled=config.sandbox.enabled,
            system_access_manager=system_access_manager,
        )
    )
    if system_access_manager is not None:
        for tool in build_host_file_tools(system_access_manager):
            registry.register(tool)
    if memory_manager is not None:
        for tool in build_memory_tools(memory_manager):
            registry.register(tool)
    browser_tools, browser_runtime = build_browser_tools(
        config,
        event_emitter=browser_event_emitter,
        viewer_checker=browser_viewer_checker,
    )
    for tool in browser_tools:
        registry.register(tool)
    registry.browser_runtime = browser_runtime
    registry.register_cleanup(browser_runtime.close)
    for tool in build_pdf_tools(config):
        registry.register(tool)
    for tool in build_search_tools(config):
        registry.register(tool)
    if model_provider is not None:
        registry.register(build_llm_task_tool(model_provider))
    if oauth_flow_manager is not None and oauth_token_manager is not None:
        for tool in build_oauth_tools(oauth_flow_manager, oauth_token_manager):
            registry.register(tool)
        for tool in build_gmail_tools(oauth_token_manager):
            registry.register(tool)
        for tool in build_github_tools(oauth_token_manager):
            registry.register(tool)
    if sub_agent_manager is not None:
        registry.register(build_agent_send_tool(sub_agent_manager))
    if acp_client is not None:
        for tool in build_acp_tools(acp_client):
            registry.register(tool)
    if sandbox_runtime is not None:
        registry.register_cleanup(sandbox_runtime.close)
    return registry
