"""Allowlisted Windows desktop actions (Settings URIs, safe primitives)."""

from __future__ import annotations

import base64
import re

# Keys must match tool / router usage exactly.
SETTINGS_PAGE_ALIASES: dict[str, str] = {
    "defaultapps": "ms-settings:defaultapps",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "apps-volume": "ms-settings:apps-volume",
    "network": "ms-settings:network",
    "network-status": "ms-settings:network-status",
    "wifi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth",
    "storage": "ms-settings:storagesense",
    "privacy": "ms-settings:privacy",
    "startup": "ms-settings:startupapps",
    "windowsupdate": "ms-settings:windowsupdate",
    "powersleep": "ms-settings:powersleep",
    "notifications": "ms-settings:notifications",
    "clipboard": "ms-settings:clipboard",
    "focusassist": "ms-settings:quiethours",
    "tablet": "ms-settings:tabletmode",
    "about": "ms-settings:about",
}


def resolve_settings_uri(page_key: str) -> str | None:
    key = (page_key or "").strip().lower()
    return SETTINGS_PAGE_ALIASES.get(key)


def explorer_open_uri_command(uri: str) -> str:
    """Launch a ms-settings: or similar URI via Explorer (reliable on user desktop sessions)."""
    safe = uri.replace("'", "''")
    return f"Start-Process explorer.exe -ArgumentList '{safe}'"


PING_HOST_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,251}[a-zA-Z0-9]$|^[a-zA-Z0-9]$")


def sanitize_ping_host(host: str) -> str | None:
    h = (host or "").strip()
    if not h or len(h) > 253:
        return None
    if not PING_HOST_PATTERN.match(h):
        return None
    return h


def volume_key_powershell(direction: str) -> str:
    """Simulate volume keys via keybd_event (relative volume, no admin)."""
    method_map = {"up": "Up", "down": "Down", "mute": "Mute"}
    method = method_map.get((direction or "").lower().strip())
    if method is None:
        raise ValueError("direction must be up, down, or mute")
    csharp = (
        "using System;using System.Runtime.InteropServices;"
        "public class Vk{"
        '[DllImport("user32.dll")]public static extern void keybd_event(byte a,byte b,int c,UIntPtr d);'
        "const int U=2;const int MU=0xAD;const int VU=0xAF;const int VD=0xAE;"
        "public static void Up(){keybd_event(VU,0,0,UIntPtr.Zero);keybd_event(VU,0,U,UIntPtr.Zero);}"
        "public static void Down(){keybd_event(VD,0,0,UIntPtr.Zero);keybd_event(VD,0,U,UIntPtr.Zero);}"
        "public static void Mute(){keybd_event(MU,0,0,UIntPtr.Zero);keybd_event(MU,0,U,UIntPtr.Zero);}"
        "}"
    )
    return f"$ErrorActionPreference='Stop'; Add-Type -TypeDefinition '{csharp}'; [Vk]::{method}()"


def lock_workstation_command() -> str:
    return "$ErrorActionPreference='Stop'; Start-Process rundll32.exe -ArgumentList 'user32.dll,LockWorkStation'"


def ping_command(host: str, count: int) -> str:
    c = max(1, min(int(count), 10))
    h = host.replace("'", "''")
    return (
        f"$ErrorActionPreference='Stop'; "
        f"Test-Connection -ComputerName '{h}' -Count {c} -ErrorAction Stop | "
        "Select-Object Address, IPV4Address, ResponseTime | Format-Table -AutoSize | Out-String -Width 4096"
    )


def _ps_single_quoted(value: str) -> str:
    return value.replace("'", "''")


def _base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _window_type_definition() -> str:
    return (
        "using System;using System.Runtime.InteropServices;"
        "public static class Win32 {"
        '[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);'
        '[DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);'
        '[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);'
        '[DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);'
        '[DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);'
        "[StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }"
        "}"
    )


def window_state_command(
    pid: int,
    action: str,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> str:
    normalized = (action or "").strip().lower()
    if normalized not in {"move", "minimize", "maximize", "restore"}:
        raise ValueError("action must be move, minimize, maximize, or restore")
    lines = [
        "$ErrorActionPreference='Stop';",
        f"$p = Get-Process -Id {int(pid)} -ErrorAction Stop;",
        "if ($p.MainWindowHandle -eq 0) { throw 'Process has no visible window.' };",
        f"Add-Type -TypeDefinition '{_window_type_definition()}';",
        "$h = $p.MainWindowHandle;",
    ]
    if normalized == "move":
        lines.extend(
            [
                "$rect = New-Object Win32+RECT;",
                "[Win32]::GetWindowRect($h, [ref]$rect) | Out-Null;",
                f"$x = {int(x) if x is not None else '$rect.Left'};",
                f"$y = {int(y) if y is not None else '$rect.Top'};",
                f"$width = {int(width) if width is not None else '($rect.Right - $rect.Left)'};",
                f"$height = {int(height) if height is not None else '($rect.Bottom - $rect.Top)'};",
                "[Win32]::MoveWindow($h, $x, $y, $width, $height, $true) | Out-Null;",
            ]
        )
    else:
        show_window_map = {"minimize": 6, "maximize": 3, "restore": 9}
        lines.append(f"[Win32]::ShowWindow($h, {show_window_map[normalized]}) | Out-Null;")
    lines.append(
        "@{ pid = $p.Id; process_name = $p.ProcessName; title = $p.MainWindowTitle; action = '" + normalized + "'; "
        + ("x = $x; y = $y; width = $width; height = $height" if normalized == "move" else "state = '" + normalized + "'")
        + " } | ConvertTo-Json -Compress"
    )
    return " ".join(lines)


def send_keys_command(keys: str) -> str:
    safe = _ps_single_quoted(keys)
    return (
        "$ErrorActionPreference='Stop'; "
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"[System.Windows.Forms.SendKeys]::SendWait('{safe}'); "
        f"@{{ keys = '{safe}' }} | ConvertTo-Json -Compress"
    )


def type_text_command(text: str) -> str:
    encoded = _base64_text(text)
    return (
        "$ErrorActionPreference='Stop'; "
        f"$previous = $null; try {{ $previous = Get-Clipboard -Raw -Format Text -ErrorAction Stop }} catch {{ $previous = $null }}; "
        f"$value = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')); "
        "$value | Set-Clipboard; "
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[System.Windows.Forms.SendKeys]::SendWait('^v'); "
        "Start-Sleep -Milliseconds 150; "
        "if ($null -ne $previous) { $previous | Set-Clipboard } else { '' | Set-Clipboard }; "
        "@{ bytes = [Text.Encoding]::UTF8.GetByteCount($value) } | ConvertTo-Json -Compress"
    )


def mouse_move_command(x: int, y: int) -> str:
    return (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -TypeDefinition '{_window_type_definition()}'; "
        f"[Win32]::SetCursorPos({int(x)}, {int(y)}) | Out-Null; "
        f"@{{ x = {int(x)}; y = {int(y)}; action = 'move' }} | ConvertTo-Json -Compress"
    )


def mouse_click_command(x: int, y: int, *, button: str = "left", clicks: int = 1) -> str:
    normalized = (button or "").strip().lower()
    if normalized not in {"left", "right", "middle"}:
        raise ValueError("button must be left, right, or middle")
    down_flag = {"left": "0x0002", "right": "0x0008", "middle": "0x0020"}[normalized]
    up_flag = {"left": "0x0004", "right": "0x0010", "middle": "0x0040"}[normalized]
    count = max(1, min(int(clicks), 10))
    return (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -TypeDefinition '{_window_type_definition()}'; "
        f"[Win32]::SetCursorPos({int(x)}, {int(y)}) | Out-Null; "
        f"for ($i = 0; $i -lt {count}; $i++) {{ [Win32]::mouse_event({down_flag}, 0, 0, 0, [UIntPtr]::Zero); [Win32]::mouse_event({up_flag}, 0, 0, 0, [UIntPtr]::Zero) }}; "
        f"@{{ x = {int(x)}; y = {int(y)}; button = '{normalized}'; clicks = {count}; action = 'click' }} | ConvertTo-Json -Compress"
    )


def mouse_scroll_command(delta: int) -> str:
    amount = int(delta)
    return (
        "$ErrorActionPreference='Stop'; "
        f"Add-Type -TypeDefinition '{_window_type_definition()}'; "
        f"[Win32]::mouse_event(0x0800, 0, 0, {amount}, [UIntPtr]::Zero); "
        f"@{{ delta = {amount}; action = 'scroll' }} | ConvertTo-Json -Compress"
    )
