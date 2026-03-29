"""Allowlisted Windows desktop actions (Settings URIs, safe primitives)."""

from __future__ import annotations

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
