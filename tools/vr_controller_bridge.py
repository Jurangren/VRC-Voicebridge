"""Quest 3 / Oculus Touch 控制器 -> 键盘组合键 桥接器（SteamVR 环境）。

为什么需要它：AutoHotkey 读不到 SteamVR 控制器（它走 OpenVR，不暴露为 Windows 标准手柄）。
本脚本以 VRApplication_Background 模式接入 SteamVR，轮询左右手柄的按钮状态，在按下/松开
时用 keyboard 库模拟出对应的键盘组合键——VRC-VoiceBridge 现有的全局热键会直接捕获这些键，
因此无需改动主程序。可与 `python main.py --vr` 同时运行（各自独立进程）。

用法：
    pip install openvr keyboard      # 项目已含这两个依赖
    python tools/vr_controller_bridge.py
    （先启动 SteamVR；某些系统下 keyboard 模拟需要管理员权限运行）

要改键位：编辑下方 BINDINGS / CHORDS。keys 用 keyboard 库语法，如 "b"、"ctrl+alt+0"。
若不确定某个按钮叫什么，加 --debug 运行，按手柄按钮即可在控制台看到识别出的名字。
"""
from __future__ import annotations

import argparse
import sys
import time

import openvr

# ---------------------------------------------------------------------------
# 键位映射 —— 按需修改这里
# ---------------------------------------------------------------------------
# hand:   "left" | "right"
# button: "A" | "B" | "grip" | "trigger" | "thumbstick"
#         （A/B 对左手即 X/Y；Quest 物理按钮在 SteamVR 下统一识别为 A=下键 B=上键）
# keys:   keyboard 库组合键字符串
# mode:   "press" 按下瞬间敲一次；"hold" 按住时持续按住该键、松开时释放（用于按住录音）
BINDINGS = [
    # 左 X：按住录音讲话（push-to-talk）-> microphone_hotkey（ctrl+g）
    {"hand": "left", "button": "X", "keys": "ctrl+g", "mode": "hold"},
]

# 组合动作：两个按钮同时按下时敲一次 keys
CHORDS = [
    # 左 Y + 扳机 -> 开/关实时翻译（speech_translate_toggle_hotkey = ctrl+alt+f9）
    {"buttons": [("left", "Y"), ("left", "trigger")], "keys": "ctrl+alt+f9"},
    # 左 Y + 握把 -> 开/关 VRChat 聊天框翻译显示（OSC，speech_translate_osc_toggle_hotkey = ctrl+alt+o）
    {"buttons": [("left", "Y"), ("left", "grip")], "keys": "ctrl+alt+o"},
    # 左 Y + 右扳机 -> 切换配置 1（preset_hotkeys[0] = ctrl+alt+1）
    {"buttons": [("left", "Y"), ("right", "trigger")], "keys": "ctrl+alt+1"},
    # 左 Y + 右握把 -> 切换配置 2（preset_hotkeys[1] = ctrl+alt+2）
    {"buttons": [("left", "Y"), ("right", "grip")], "keys": "ctrl+alt+2"},
]

POLL_HZ = 60          # 轮询频率
TRIGGER_THRESHOLD = 0.6   # 扳机/握把模拟轴的判定阈值（部分手柄不发按钮位，靠模拟轴）

# ---------------------------------------------------------------------------
# 以下一般无需改动
# ---------------------------------------------------------------------------

# OpenVR 旧版输入按钮位（对 Oculus Touch via SteamVR 的映射）
BUTTON_BITS = {
    "A": 1 << openvr.k_EButton_A,                    # 下键（右手 A）
    "B": 1 << openvr.k_EButton_ApplicationMenu,      # 上键（右手 B）
    "X": 1 << openvr.k_EButton_A,                    # 左手下键（物理 X，与 A 同位）
    "Y": 1 << openvr.k_EButton_ApplicationMenu,      # 左手上键（物理 Y，与 B 同位）
    "grip": 1 << openvr.k_EButton_Grip,
    "trigger": 1 << openvr.k_EButton_SteamVR_Trigger,    # = Axis1
    "thumbstick": 1 << openvr.k_EButton_SteamVR_Touchpad,  # = Axis0，摇杆按下
}
# 模拟轴下标：握把/扳机在部分运行时只发模拟轴而不发按钮位，用阈值兜底
AXIS_INDEX = {"trigger": 1, "grip": 2}

HAND_ROLE = {
    "left": openvr.TrackedControllerRole_LeftHand,
    "right": openvr.TrackedControllerRole_RightHand,
}

try:
    import keyboard
except Exception as exc:  # pragma: no cover
    print(f"未安装 keyboard 库：{exc}\n请先 pip install keyboard", file=sys.stderr)
    sys.exit(1)


def find_controllers(vrsystem):
    """返回 {"left": device_index, "right": device_index}，缺失的手不在字典里。"""
    found = {}
    for idx in range(openvr.k_unMaxTrackedDeviceCount):
        if vrsystem.getTrackedDeviceClass(idx) != openvr.TrackedDeviceClass_Controller:
            continue
        role = vrsystem.getControllerRoleForTrackedDeviceIndex(idx)
        if role == openvr.TrackedControllerRole_LeftHand:
            found["left"] = idx
        elif role == openvr.TrackedControllerRole_RightHand:
            found["right"] = idx
    return found


def read_pressed(vrsystem, idx) -> set[str]:
    """读取某只手柄当前按下的按钮名集合。"""
    result, state = vrsystem.getControllerState(idx)
    if not result:
        return set()
    pressed = set()
    for name, bit in BUTTON_BITS.items():
        if state.ulButtonPressed & bit:
            pressed.add(name)
    # 模拟轴兜底：扳机/握把
    for name, axis in AXIS_INDEX.items():
        try:
            if state.rAxis[axis].x >= TRIGGER_THRESHOLD:
                pressed.add(name)
        except Exception:
            pass
    return pressed


def main() -> int:
    parser = argparse.ArgumentParser(description="VR 控制器 -> 键盘组合键 桥接器")
    parser.add_argument("--debug", action="store_true", help="打印识别到的按钮，用于确认键位")
    args = parser.parse_args()

    try:
        openvr.init(openvr.VRApplication_Background)
    except Exception as exc:
        print(f"无法连接 SteamVR，请先启动 SteamVR。\n（{exc}）", file=sys.stderr)
        return 1
    vrsystem = openvr.VRSystem()
    print("已连接 SteamVR。按 Ctrl+C 退出。")

    # 每个 (hand, button) 的上一帧按下状态，用于检测按下/松开边沿
    prev: dict[tuple[str, str], bool] = {}
    held_keys: dict[tuple[str, str], str] = {}  # hold 模式正在按住的键
    chord_active: dict[int, bool] = {}          # 各 chord 是否处于已触发状态
    period = 1.0 / POLL_HZ

    try:
        while True:
            controllers = find_controllers(vrsystem)
            # 当前各手按下的按钮集合
            pressed_by_hand: dict[str, set[str]] = {
                hand: read_pressed(vrsystem, idx) for hand, idx in controllers.items()
            }

            if args.debug:
                for hand, names in pressed_by_hand.items():
                    if names:
                        print(f"[{hand}] {sorted(names)}")

            # 先判定组合键：被组合占用的按钮本帧不再走单键逻辑
            consumed: set[tuple[str, str]] = set()
            for ci, chord in enumerate(CHORDS):
                btns = [tuple(b) for b in chord["buttons"]]
                all_down = all(
                    b[1] in pressed_by_hand.get(b[0], set()) for b in btns
                )
                if all_down and not chord_active.get(ci):
                    keyboard.send(chord["keys"])
                    chord_active[ci] = True
                    print(f"  -> chord {chord['keys']}")
                elif not all_down:
                    chord_active[ci] = False
                if all_down:
                    consumed.update(btns)

            # 单键绑定：检测按下/松开边沿
            for b in BINDINGS:
                key = (b["hand"], b["button"])
                down = b["button"] in pressed_by_hand.get(b["hand"], set())
                was = prev.get(key, False)

                if key in consumed:
                    # 被组合占用：若之前是 hold 按住状态，松开它
                    if held_keys.get(key):
                        keyboard.release(held_keys.pop(key))
                    prev[key] = down
                    continue

                if b["mode"] == "press":
                    if down and not was:
                        keyboard.send(b["keys"])
                        print(f"  -> press {b['keys']}")
                elif b["mode"] == "hold":
                    if down and not was:
                        keyboard.press(b["keys"])
                        held_keys[key] = b["keys"]
                        print(f"  -> hold down {b['keys']}")
                    elif not down and was and held_keys.get(key):
                        keyboard.release(held_keys.pop(key))
                        print(f"  -> hold up {b['keys']}")
                prev[key] = down

            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        for k in list(held_keys.values()):
            try:
                keyboard.release(k)
            except Exception:
                pass
        openvr.shutdown()
        print("\n已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
