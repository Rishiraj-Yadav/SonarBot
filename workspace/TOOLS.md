# Tool Notes

Only use tools that are necessary for the task. Respect workspace path boundaries.

On Windows, when the user asks to change **screen brightness**, call **`set_windows_brightness`** with `percent` (0–100). Do not rely on generic `exec_shell` for this. External-only monitors often do not support WMI brightness; say so if the tool reports no `WmiMonitorBrightnessMethods` instance.

Changing the **default web browser** is handled by the gateway (it opens Windows Default apps). You cannot set it silently; the user must confirm in Settings.
