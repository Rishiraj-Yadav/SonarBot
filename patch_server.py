"""One-time patch script: inserts automation tool registration into server.py"""
import pathlib

path = pathlib.Path("assistant/gateway/server.py")
content = path.read_bytes().decode("utf-8")

# The text we want to insert BEFORE
marker = (
    "        app.state.services = GatewayServices(\r\n"
    "            config=runtime_config,"
)
# The guard so we don't double-patch
guard = "build_automation_tools"

if guard in content:
    print("Already patched – nothing to do.")
elif marker not in content:
    print("ERROR: marker not found in server.py")
    # Print a nearby excerpt to help debug
    idx = content.find("heartbeat_service = HeartbeatService")
    print(repr(content[idx:idx+300]))
else:
    injection = (
        "        # Register automation tools so the LLM can create/list/delete reminders\r\n"
        "        from assistant.tools.automation_tool import build_automation_tools\r\n"
        "        for _auto_tool in build_automation_tools(automation_engine):\r\n"
        "            tool_registry.register(_auto_tool)\r\n"
    )
    content = content.replace(marker, injection + marker, 1)
    path.write_bytes(content.encode("utf-8"))
    print("PATCHED OK")
