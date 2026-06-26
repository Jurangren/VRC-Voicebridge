from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import openvr


APP_KEY = "vrc.voicebridge.controller_bridge"
APP_NAME = "VRC VoiceBridge Controller Bridge"
APP_DESCRIPTION = "Maps SteamVR controller input to VRC VoiceBridge keyboard hotkeys."


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def manifest_path() -> Path:
    return repo_root() / ".steamvr" / "vrc_voicebridge_controller_bridge.vrmanifest"


def default_log_path() -> Path:
    return repo_root() / ".steamvr" / "vr_controller_bridge.log"


def preferred_python_binary() -> Path:
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            return pythonw
    return executable


def build_manifest(python_binary: Path) -> dict:
    bridge_script = repo_root() / "tools" / "vr_controller_bridge.py"
    args = subprocess.list2cmdline(
        [
            str(bridge_script),
            "--quiet",
            "--log-file",
            str(default_log_path()),
        ]
    )
    return {
        "applications": [
            {
                "app_key": APP_KEY,
                "launch_type": "binary",
                "binary_path_windows": str(python_binary),
                "arguments": args,
                "is_dashboard_overlay": True,
                "strings": {
                    "en_us": {
                        "name": APP_NAME,
                        "description": APP_DESCRIPTION,
                    },
                    "zh_cn": {
                        "name": APP_NAME,
                        "description": "把 SteamVR 手柄输入映射为 VRC VoiceBridge 热键。",
                    },
                },
            }
        ],
    }


def write_manifest(python_binary: Path | None = None) -> Path:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = build_manifest((python_binary or preferred_python_binary()).resolve())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class OpenVRSession:
    def __enter__(self):
        try:
            openvr.init(openvr.VRApplication_Utility)
        except Exception as exc:
            raise RuntimeError(f"无法连接 SteamVR。请先启动 SteamVR，再运行本命令。原始错误：{exc!r}") from exc
        return openvr.VRApplications()

    def __exit__(self, exc_type, exc, tb) -> None:
        openvr.shutdown()


def remove_manifest_if_present(apps, path: Path) -> None:
    try:
        apps.removeApplicationManifest(str(path))
    except Exception:
        pass


def install(args: argparse.Namespace) -> int:
    python_binary = Path(args.python).resolve() if args.python else None
    path = write_manifest(python_binary)
    with OpenVRSession() as apps:
        remove_manifest_if_present(apps, path)
        apps.addApplicationManifest(str(path), False)
        installed = bool(apps.isApplicationInstalled(APP_KEY))
        if not installed:
            raise RuntimeError(
                "SteamVR 已接收清单路径，但没有识别出应用。"
                "请确认清单里的 binary_path_windows 指向可执行文件。"
            )
        apps.setApplicationAutoLaunch(APP_KEY, True)
        autostart = bool(apps.getApplicationAutoLaunch(APP_KEY))
    print(f"已注册 SteamVR 应用：{APP_NAME}")
    print(f"应用键：{APP_KEY}")
    print(f"清单：{path}")
    print(f"已安装：{installed}")
    print(f"随 SteamVR 自动启动：{autostart}")
    return 0 if installed and autostart else 1


def uninstall(_args: argparse.Namespace) -> int:
    path = manifest_path()
    with OpenVRSession() as apps:
        if apps.isApplicationInstalled(APP_KEY):
            apps.setApplicationAutoLaunch(APP_KEY, False)
        if path.exists():
            remove_manifest_if_present(apps, path)
        installed = bool(apps.isApplicationInstalled(APP_KEY))
    print(f"已取消注册 SteamVR 应用：{APP_NAME}")
    print(f"仍显示已安装：{installed}")
    return 1 if installed else 0


def status(_args: argparse.Namespace) -> int:
    path = manifest_path()
    print(f"应用键：{APP_KEY}")
    print(f"清单：{path}")
    print(f"清单存在：{path.exists()}")
    with OpenVRSession() as apps:
        installed = bool(apps.isApplicationInstalled(APP_KEY))
        autostart = bool(apps.getApplicationAutoLaunch(APP_KEY)) if installed else False
    print(f"已安装：{installed}")
    print(f"随 SteamVR 自动启动：{autostart}")
    return 0


def write(_args: argparse.Namespace) -> int:
    path = write_manifest()
    print(f"已写入 SteamVR 应用清单：{path}")
    return 0


def launch(_args: argparse.Namespace) -> int:
    with OpenVRSession() as apps:
        if not apps.isApplicationInstalled(APP_KEY):
            print("SteamVR 应用尚未注册，请先运行 install。", file=sys.stderr)
            return 1
        apps.launchApplication(APP_KEY)
    print(f"已请求 SteamVR 启动：{APP_NAME}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="注册 VRC VoiceBridge SteamVR 手柄桥接应用")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="生成清单、注册应用并开启随 SteamVR 启动")
    install_parser.add_argument("--python", help="指定用于启动桥接器的 Python/Pythonw 路径")
    install_parser.set_defaults(func=install)

    uninstall_parser = subparsers.add_parser("uninstall", help="取消随 SteamVR 启动并移除应用注册")
    uninstall_parser.set_defaults(func=uninstall)

    status_parser = subparsers.add_parser("status", help="查看 SteamVR 注册状态")
    status_parser.set_defaults(func=status)

    write_parser = subparsers.add_parser("write", help="只生成本机 .vrmanifest，不注册")
    write_parser.set_defaults(func=write)

    launch_parser = subparsers.add_parser("launch", help="通过 SteamVR 启动已注册的桥接器")
    launch_parser.set_defaults(func=launch)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"失败：{exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
