"""Windows settings and basic system control skill pack."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SETTINGS_PAGES = {
    "settings": "ms-settings:",
    "sound": "ms-settings:sound",
    "volume": "ms-settings:sound",
    "brightness": "ms-settings:display",
    "display": "ms-settings:display",
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "network": "ms-settings:network",
    "notifications": "ms-settings:notifications",
}


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass(slots=True)
class BluetoothSummary:
    available: bool
    service_status: str
    device_count: int


class SystemControlPack:
    def __init__(self, config, system_access_manager=None) -> None:
        self.config = config
        self.system_access_manager = system_access_manager
        self._user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
        self._winmm = ctypes.WinDLL("winmm", use_last_error=True) if sys.platform == "win32" else None
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None

    def ensure_enabled(self) -> None:
        if not bool(getattr(self.config.app_skills, "enabled", False)):
            raise RuntimeError("App skills are not enabled.")
        if not bool(getattr(self.config.app_skills, "system_enabled", True)):
            raise RuntimeError("System control pack is disabled.")
        if sys.platform != "win32":
            raise RuntimeError("System controls are only available on Windows hosts.")

    def open_settings(self, page: str) -> dict[str, Any]:
        self.ensure_enabled()
        normalized = page.strip().lower()
        uri = SETTINGS_PAGES.get(normalized)
        if uri is None:
            raise RuntimeError(f"Unknown settings page '{page}'.")
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
        return {"page": normalized, "uri": uri, "status": "completed"}

    def volume_status(self) -> dict[str, Any]:
        self.ensure_enabled()
        if self._winmm is None:
            raise RuntimeError("System volume controls are unavailable.")
        current = wintypes.DWORD()
        result = self._winmm.waveOutGetVolume(0xFFFFFFFF, ctypes.byref(current))
        if result != 0:
            raise RuntimeError("Unable to read the current system volume.")
        left = current.value & 0xFFFF
        right = (current.value >> 16) & 0xFFFF
        percent = round((((left + right) / 2) / 0xFFFF) * 100)
        return {"volume_percent": int(percent), "left_percent": round(left / 0xFFFF * 100), "right_percent": round(right / 0xFFFF * 100)}

    def set_volume(self, percent: int) -> dict[str, Any]:
        self.ensure_enabled()
        if self._winmm is None:
            raise RuntimeError("System volume controls are unavailable.")
        clamped = max(0, min(100, int(percent)))
        raw = int(clamped / 100 * 0xFFFF)
        packed = (raw << 16) | raw
        result = self._winmm.waveOutSetVolume(0xFFFFFFFF, packed)
        if result != 0:
            raise RuntimeError("Unable to change the system volume.")
        return {"status": "completed", "volume_percent": clamped}

    def brightness_status(self) -> dict[str, Any]:
        self.ensure_enabled()
        payload = self._powershell_json(
            "$b = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 CurrentBrightness,Levels; "
            "if ($null -eq $b) { @{supported=$false} | ConvertTo-Json -Compress } "
            "else { @{supported=$true; current=$b.CurrentBrightness; levels=$b.Levels} | ConvertTo-Json -Compress }"
        )
        if not payload or not payload.get("supported"):
            return {"supported": False, "message": "Direct brightness control is unavailable on this device."}
        return {
            "supported": True,
            "brightness_percent": int(payload.get("current", 0)),
            "levels": int(payload.get("levels", 0)),
        }

    def set_brightness(self, percent: int) -> dict[str, Any]:
        self.ensure_enabled()
        clamped = max(0, min(100, int(percent)))
        payload = self._powershell_json(
            f"$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue | Select-Object -First 1; "
            f"if ($null -eq $m) {{ @{{supported=$false}} | ConvertTo-Json -Compress }} "
            f"else {{ $null = Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{{Brightness={clamped}; Timeout=1}}; "
            f"@{{supported=$true; brightness={clamped}}} | ConvertTo-Json -Compress }}"
        )
        if not payload or not payload.get("supported"):
            raise RuntimeError("Direct brightness control is unavailable on this device.")
        return {"status": "completed", "brightness_percent": clamped}

    def bluetooth_status(self) -> dict[str, Any]:
        self.ensure_enabled()
        radio = self._bluetooth_radio_snapshot()
        payload = self._powershell_json(
            "$service = Get-Service bthserv -ErrorAction SilentlyContinue; "
            "$devices = @(Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'OK' }); "
            "@{service_status = if ($service) { $service.Status.ToString() } else { 'Unknown' }; "
            "device_count = $devices.Count; available = ($devices.Count -gt 0)} | ConvertTo-Json -Compress"
        )
        if not payload:
            return {"available": False, "service_status": "Unknown", "device_count": 0}
        summary = BluetoothSummary(
            available=bool(payload.get("available", False)),
            service_status=str(payload.get("service_status", "Unknown")),
            device_count=int(payload.get("device_count", 0)),
        )
        return {
            "available": bool(summary.available or radio.get("present", False)),
            "service_status": summary.service_status,
            "device_count": summary.device_count,
            "radio_state": str(radio.get("state", "Unknown")),
            "direct_control_supported": bool(radio.get("supported", False)),
            "direct_control_error": str(radio.get("error", "")).strip(),
        }

    def set_bluetooth(self, mode: str) -> dict[str, Any]:
        self.ensure_enabled()
        normalized = str(mode).strip().lower()
        if normalized not in {"on", "off", "toggle"}:
            raise RuntimeError("Bluetooth mode must be on, off, or toggle.")
        before = self._bluetooth_radio_snapshot()
        if not before.get("supported") or not before.get("present"):
            return {
                "status": "unsupported",
                "supported": False,
                "requested_state": normalized,
                "radio_state_before": str(before.get("state", "Unknown")),
                "radio_state_after": str(before.get("state", "Unknown")),
                "message": str(before.get("error") or "Direct Bluetooth control is unavailable on this device."),
            }

        before_state = str(before.get("state", "Unknown")).strip().lower()
        desired_state = normalized
        if normalized == "toggle":
            if before_state == "on":
                desired_state = "off"
            elif before_state == "off":
                desired_state = "on"
            else:
                return {
                    "status": "unsupported",
                    "supported": False,
                    "requested_state": normalized,
                    "radio_state_before": str(before.get("state", "Unknown")),
                    "radio_state_after": str(before.get("state", "Unknown")),
                    "message": "Direct Bluetooth toggle is unavailable because the current Bluetooth radio state is unknown.",
                }

        payload = self._powershell_json(self._bluetooth_set_script(desired_state))
        after_state = str(payload.get("after_state", before.get("state", "Unknown")))
        access_status = str(payload.get("access_status", "")).strip()
        success = (
            bool(payload.get("supported", False))
            and after_state.strip().lower() == desired_state
        )
        return {
            "status": "completed" if success else ("unsupported" if not payload.get("supported", False) else "failed"),
            "supported": bool(payload.get("supported", False)),
            "requested_state": desired_state,
            "radio_state_before": str(payload.get("before_state", before.get("state", "Unknown"))),
            "radio_state_after": after_state,
            "access_status": access_status,
            "message": "" if success else str(payload.get("error") or payload.get("message") or "Bluetooth state did not change."),
        }

    def system_snapshot(self) -> dict[str, Any]:
        self.ensure_enabled()
        return {
            "cpu_percent": self._cpu_percent(),
            "memory": self._memory_status(),
            "disk": self._disk_status(),
            "bluetooth": self.bluetooth_status(),
            "volume": self.volume_status(),
        }

    def _powershell_json(self, command: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
        except Exception:
            return {}
        raw = (completed.stdout or "").strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _bluetooth_radio_snapshot(self) -> dict[str, Any]:
        self.ensure_enabled()
        payload = self._powershell_json(self._bluetooth_snapshot_script())
        if payload:
            return payload
        return {"supported": False, "present": False, "state": "Unknown", "error": "Unable to query the Bluetooth radio."}

    def _bluetooth_snapshot_script(self) -> str:
        return (
            "$ErrorActionPreference = 'Stop'; "
            "try { "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null; "
            "$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | "
            "Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } | "
            "Select-Object -First 1; "
            "if ($null -eq $asTask) { throw 'AsTask helper unavailable.' }; "
            "function Await-WinRT($Operation, [string]$TypeName) { "
            "$type = [Type]::GetType($TypeName, $false); "
            "if ($null -eq $type) { throw ('Missing type ' + $TypeName) }; "
            "$method = $asTask.MakeGenericMethod($type); "
            "$task = $method.Invoke($null, @($Operation)); "
            "$task.Wait(-1) | Out-Null; "
            "return $task.Result; "
            "}; "
            "$radioType = [Type]::GetType('Windows.Devices.Radios.Radio, Windows, ContentType=WindowsRuntime', $false); "
            "if ($null -eq $radioType) { throw 'Bluetooth radio type unavailable.' }; "
            "$radios = Await-WinRT ($radioType::GetRadiosAsync()) 'System.Collections.Generic.IReadOnlyList`1[[Windows.Devices.Radios.Radio, Windows, ContentType=WindowsRuntime]]'; "
            "$radio = $radios | Where-Object { $_.Kind.ToString() -eq 'Bluetooth' } | Select-Object -First 1; "
            "if ($null -eq $radio) { @{supported=$false; present=$false; state='Unknown'} | ConvertTo-Json -Compress; exit 0 }; "
            "@{supported=$true; present=$true; state=$radio.State.ToString()} | ConvertTo-Json -Compress "
            "} catch { "
            "@{supported=$false; present=$false; state='Unknown'; error=$_.Exception.Message} | ConvertTo-Json -Compress "
            "}"
        )

    def _bluetooth_set_script(self, desired_state: str) -> str:
        normalized = "On" if desired_state.strip().lower() == "on" else "Off"
        return (
            "$ErrorActionPreference = 'Stop'; "
            "try { "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null; "
            "$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | "
            "Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } | "
            "Select-Object -First 1; "
            "if ($null -eq $asTask) { throw 'AsTask helper unavailable.' }; "
            "function Await-WinRT($Operation, [string]$TypeName) { "
            "$type = [Type]::GetType($TypeName, $false); "
            "if ($null -eq $type) { throw ('Missing type ' + $TypeName) }; "
            "$method = $asTask.MakeGenericMethod($type); "
            "$task = $method.Invoke($null, @($Operation)); "
            "$task.Wait(-1) | Out-Null; "
            "return $task.Result; "
            "}; "
            "$radioType = [Type]::GetType('Windows.Devices.Radios.Radio, Windows, ContentType=WindowsRuntime', $false); "
            "$stateType = [Type]::GetType('Windows.Devices.Radios.RadioState, Windows, ContentType=WindowsRuntime', $false); "
            "if ($null -eq $radioType -or $null -eq $stateType) { throw 'Bluetooth radio types unavailable.' }; "
            "$radios = Await-WinRT ($radioType::GetRadiosAsync()) 'System.Collections.Generic.IReadOnlyList`1[[Windows.Devices.Radios.Radio, Windows, ContentType=WindowsRuntime]]'; "
            "$radio = $radios | Where-Object { $_.Kind.ToString() -eq 'Bluetooth' } | Select-Object -First 1; "
            "if ($null -eq $radio) { @{supported=$false; present=$false; before_state='Unknown'; after_state='Unknown'; message='Bluetooth radio not found.'} | ConvertTo-Json -Compress; exit 0 }; "
            "$before = $radio.State.ToString(); "
            "$desired = [Enum]::Parse($stateType, '" + normalized + "'); "
            "$access = Await-WinRT ($radio.SetStateAsync($desired)) 'Windows.Devices.Radios.RadioAccessStatus, Windows, ContentType=WindowsRuntime'; "
            "Start-Sleep -Milliseconds 300; "
            "$radiosAfter = Await-WinRT ($radioType::GetRadiosAsync()) 'System.Collections.Generic.IReadOnlyList`1[[Windows.Devices.Radios.Radio, Windows, ContentType=WindowsRuntime]]'; "
            "$radioAfter = $radiosAfter | Where-Object { $_.Kind.ToString() -eq 'Bluetooth' } | Select-Object -First 1; "
            "$after = if ($null -ne $radioAfter) { $radioAfter.State.ToString() } else { 'Unknown' }; "
            "@{supported=$true; present=$true; before_state=$before; after_state=$after; access_status=$access.ToString()} | ConvertTo-Json -Compress "
            "} catch { "
            "@{supported=$false; present=$false; before_state='Unknown'; after_state='Unknown'; error=$_.Exception.Message} | ConvertTo-Json -Compress "
            "}"
        )

    def _cpu_percent(self) -> float:
        if self._kernel32 is None:
            return 0.0
        idle_1, kernel_1, user_1 = FILETIME(), FILETIME(), FILETIME()
        idle_2, kernel_2, user_2 = FILETIME(), FILETIME(), FILETIME()
        self._kernel32.GetSystemTimes(ctypes.byref(idle_1), ctypes.byref(kernel_1), ctypes.byref(user_1))
        time.sleep(0.15)
        self._kernel32.GetSystemTimes(ctypes.byref(idle_2), ctypes.byref(kernel_2), ctypes.byref(user_2))
        idle_delta = _filetime_value(idle_2) - _filetime_value(idle_1)
        kernel_delta = _filetime_value(kernel_2) - _filetime_value(kernel_1)
        user_delta = _filetime_value(user_2) - _filetime_value(user_1)
        total = max(1, kernel_delta + user_delta)
        busy = max(0, total - idle_delta)
        return round((busy / total) * 100, 1)

    def _memory_status(self) -> dict[str, Any]:
        if self._kernel32 is None:
            return {"used_percent": 0.0}
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not self._kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {"used_percent": 0.0}
        used = status.ullTotalPhys - status.ullAvailPhys
        return {
            "used_percent": round((used / max(1, status.ullTotalPhys)) * 100, 1),
            "used_gb": round(used / (1024**3), 1),
            "total_gb": round(status.ullTotalPhys / (1024**3), 1),
        }

    def _disk_status(self) -> dict[str, Any]:
        drive = Path.home().anchor or "C:\\"
        total, used, free = shutil.disk_usage(drive)
        return {
            "drive": drive,
            "used_percent": round((used / max(1, total)) * 100, 1),
            "free_gb": round(free / (1024**3), 1),
            "total_gb": round(total / (1024**3), 1),
        }


def _filetime_value(value: FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime
