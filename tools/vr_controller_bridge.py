from __future__ import annotations

import argparse
import ctypes
import os
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import sys
import time

import openvr

try:
    import keyboard
except Exception as exc:  # pragma: no cover
    print(f"keyboard package is not installed: {exc}\nPlease run: pip install keyboard", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Controller mappings. Edit these when you want to change VR button behavior.
# ---------------------------------------------------------------------------
# hand:   "left" | "right"
# button: "A" | "B" | "X" | "Y" | "grip" | "trigger" | "thumbstick"
# keys:   keyboard library hotkey string, for example "b" or "ctrl+alt+0"
# mode:   "press" sends once on button down; "hold" presses on down and releases on up.
BINDINGS = [
    # Left X: push-to-talk recording -> microphone_hotkey (ctrl+g)
    {"hand": "left", "button": "X", "keys": "ctrl+g", "mode": "hold"},
]

# Chord actions: all listed buttons must be held at the same time.
CHORDS = [
    # Left Y + trigger -> toggle realtime translation
    {"buttons": [("left", "Y"), ("left", "trigger")], "keys": "ctrl+alt+f9"},
    # Left Y + grip -> toggle VRChat chatbox translation display (OSC)
    {"buttons": [("left", "Y"), ("left", "grip")], "keys": "ctrl+alt+o"},
    # Left Y + right trigger -> switch preset 1
    {"buttons": [("left", "Y"), ("right", "trigger")], "keys": "ctrl+alt+1"},
    # Left Y + right grip -> switch preset 2
    {"buttons": [("left", "Y"), ("right", "grip")], "keys": "ctrl+alt+2"},
    # Left B + right B -> image translation (capture VRChat window -> Baidu image translate -> show in VR).
    # Pressing right B alongside left B makes the menu state machine treat left B as a chord
    # modifier, so this never accidentally opens the quick menu.
    {"buttons": [("left", "B"), ("right", "B")], "keys": "ctrl+alt+i"},
]

POLL_HZ = 60
TRIGGER_THRESHOLD = 0.6
MUTEX_NAME = "Local\\VRCVoiceBridgeSteamVRControllerBridge"

# Legacy OpenVR button bits for Oculus Touch via SteamVR.
BUTTON_BITS = {
    "A": 1 << openvr.k_EButton_A,
    "B": 1 << openvr.k_EButton_ApplicationMenu,
    "X": 1 << openvr.k_EButton_A,
    "Y": 1 << openvr.k_EButton_ApplicationMenu,
    "grip": 1 << openvr.k_EButton_Grip,
    "trigger": 1 << openvr.k_EButton_SteamVR_Trigger,
    "thumbstick": 1 << openvr.k_EButton_SteamVR_Touchpad,
}
AXIS_INDEX = {"trigger": 1, "grip": 2}

HAND_ROLE = {
    "left": openvr.TrackedControllerRole_LeftHand,
    "right": openvr.TrackedControllerRole_RightHand,
}


class SingleInstance:
    def __init__(self, name: str):
        self.name = name
        self._kernel32 = None
        self._handle = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self._kernel32 is not None and self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def build_logger(quiet: bool, log_file: str | None):
    path = Path(log_file) if log_file else None

    def log(message: str, *, error: bool = False, force: bool = False) -> None:
        if not quiet or force:
            print(message, file=sys.stderr if error else sys.stdout)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with path.open("a", encoding="utf-8") as file:
                    file.write(f"[{stamp}] {message}\n")
            except Exception:
                pass

    return log


def find_controllers(vrsystem) -> dict[str, int]:
    found: dict[str, int] = {}
    for idx in range(openvr.k_unMaxTrackedDeviceCount):
        if vrsystem.getTrackedDeviceClass(idx) != openvr.TrackedDeviceClass_Controller:
            continue
        role = vrsystem.getControllerRoleForTrackedDeviceIndex(idx)
        if role == openvr.TrackedControllerRole_LeftHand:
            found["left"] = idx
        elif role == openvr.TrackedControllerRole_RightHand:
            found["right"] = idx
    return found


def read_pressed(vrsystem, idx: int) -> set[str]:
    result, state = vrsystem.getControllerState(idx)
    if not result:
        return set()
    pressed = set()
    for name, bit in BUTTON_BITS.items():
        if state.ulButtonPressed & bit:
            pressed.add(name)
    for name, axis in AXIS_INDEX.items():
        try:
            if state.rAxis[axis].x >= TRIGGER_THRESHOLD:
                pressed.add(name)
        except Exception:
            pass
    return pressed


def pid_path(override: str | None = None) -> Path:
    # 主程序据此判断链接器是否在运行、并在需要时结束它。打包成 exe 时路径由主程序经 --pid-file 指定，
    # 以保证两边一致（源码运行时默认放仓库根的 .steamvr/）。
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / ".steamvr" / "vr_controller_bridge.pid"


def write_pid_file(override: str | None = None) -> None:
    try:
        path = pid_path(override)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def remove_pid_file(override: str | None = None) -> None:
    try:
        pid_path(override).unlink(missing_ok=True)
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SteamVR controller to keyboard hotkey bridge")
    parser.add_argument("--debug", action="store_true", help="print detected controller buttons")
    parser.add_argument("--quiet", action="store_true", help="suppress console output for SteamVR autostart")
    parser.add_argument("--log-file", help="append bridge status and actions to a log file")
    parser.add_argument("--pid-file", help="write this process's PID here while running (for the app to track it)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log = build_logger(args.quiet, args.log_file)
    instance = SingleInstance(MUTEX_NAME)
    if not instance.acquire():
        log("VRC VoiceBridge controller bridge is already running.")
        return 0
    write_pid_file(args.pid_file)

    try:
        try:
            openvr.init(openvr.VRApplication_Background)
        except Exception as exc:
            log(f"Cannot connect to SteamVR. Start SteamVR first. ({exc})", error=True, force=not args.quiet)
            return 1

        vrsystem = openvr.VRSystem()
        log("Connected to SteamVR. Press Ctrl+C to exit.")

        prev: dict[tuple[str, str], bool] = {}
        held_keys: dict[tuple[str, str], str] = {}
        chord_active: dict[int, bool] = {}
        period = 1.0 / POLL_HZ

        try:
            event = openvr.VREvent_t()
            while True:
                # SteamVR 退出时会发 VREvent_Quit -> 主动结束，释放单实例锁；
                # 这样重启 SteamVR 能干净地自动拉起新代码（否则旧进程占着锁会挡住新实例）。
                got_quit = False
                while vrsystem.pollNextEvent(event):
                    if event.eventType == openvr.VREvent_Quit:
                        got_quit = True
                if got_quit:
                    log("SteamVR 已退出，桥接器结束。")
                    try:
                        vrsystem.acknowledgeQuit_Exiting()
                    except Exception:
                        pass
                    break

                controllers = find_controllers(vrsystem)
                pressed_by_hand = {
                    hand: read_pressed(vrsystem, idx) for hand, idx in controllers.items()
                }

                if args.debug:
                    for hand, names in pressed_by_hand.items():
                        if names:
                            log(f"[{hand}] {sorted(names)}")

                consumed: set[tuple[str, str]] = set()
                for ci, chord in enumerate(CHORDS):
                    buttons = [tuple(button) for button in chord["buttons"]]
                    all_down = all(
                        button in pressed_by_hand.get(hand, set()) for hand, button in buttons
                    )
                    if all_down and not chord_active.get(ci):
                        keyboard.send(chord["keys"])
                        chord_active[ci] = True
                        log(f"-> chord {chord['keys']}")
                    elif not all_down:
                        chord_active[ci] = False
                    if all_down:
                        consumed.update(buttons)

                for binding in BINDINGS:
                    key = (binding["hand"], binding["button"])
                    down = binding["button"] in pressed_by_hand.get(binding["hand"], set())
                    was = prev.get(key, False)

                    if key in consumed:
                        if held_keys.get(key):
                            keyboard.release(held_keys.pop(key))
                        prev[key] = down
                        continue

                    if binding["mode"] == "press":
                        if down and not was:
                            keyboard.send(binding["keys"])
                            log(f"-> press {binding['keys']}")
                    elif binding["mode"] == "hold":
                        if down and not was:
                            keyboard.press(binding["keys"])
                            held_keys[key] = binding["keys"]
                            log(f"-> hold down {binding['keys']}")
                        elif not down and was and held_keys.get(key):
                            keyboard.release(held_keys.pop(key))
                            log(f"-> hold up {binding['keys']}")
                    prev[key] = down

                time.sleep(period)
        except KeyboardInterrupt:
            pass
        finally:
            for keys in list(held_keys.values()):
                try:
                    keyboard.release(keys)
                except Exception:
                    pass
            openvr.shutdown()
            log("Controller bridge exited.")
    finally:
        remove_pid_file(args.pid_file)
        instance.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
