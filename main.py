from __future__ import annotations

import queue
import signal
import sys
import tkinter as tk
import threading
import time
import webbrowser

from core.config import PRESET_COUNT, ConfigManager
from core.errors import ErrorHandler
from core.pipeline import AppPipeline
from services.bridge_controller import BridgeController
from services.mic_listener import MicrophoneListener
from services.osc_client import VrcOscClient
from ui.control_window import ControlWindow
from ui.hotkey import HotkeyManager
from ui.input_window import InputWindow
from ui.overlay_router import OverlayRouter
from ui.speech_indicator import SpeechIndicator
from ui.status_overlay import StatusOverlay
from ui.tray_app import TrayApp
from web.server import create_web_app, start_web_server
from services.mic_vad_listener import VadMicListener
from services.output_capture import output_capture_status
from services.realtime_pipeline import PIPELINE


class Application:
    TYPING_REFRESH_MS = 1500

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("VRC VoiceBridge")

        self.config_manager = ConfigManager()
        self.error_handler = ErrorHandler(self.root)
        # 主线程调度队列（_post 使用）：keyboard 热键回调/托盘/flask 都在各自线程里，绝不能直接碰
        # tkinter（含 root.after），否则跑一段时间后 Tk 事件循环会被弄挂。需在 OverlayRouter 之前就绪。
        self._call_queue: queue.Queue = queue.Queue()
        # 统一启动器：始终先建桌面浮窗，再用 OverlayRouter 在桌面/VR 之间切换显示目标——
        # 连上 SteamVR 时切到 VR overlay，断开时回到桌面。pipeline/web 持有 router（稳定引用），
        # 不必在接入/断开时重新接线。
        self._desktop_status = StatusOverlay(self.root, self.config_manager)
        self._desktop_speech = SpeechIndicator(self.root, self._speech_capture_status)
        self.overlay_router = OverlayRouter(self._desktop_status, self._desktop_speech, self._post)
        self.status_overlay = self.overlay_router
        self.speech_indicator = self.overlay_router
        self.vr_ui = None
        self.bridge = BridgeController()
        self.pipeline = AppPipeline(
            self.config_manager,
            self.error_handler,
            progress_callback=self.status_overlay.show_progress,
            done_callback=self.status_overlay.show_done,
            error_callback=self.status_overlay.show_error,
            before_audio_callback=lambda: self._post(self.hide_typing_bubble),
            finish_callback=lambda: self._post(self.hide_typing_bubble),
        )
        self.input_window = InputWindow(
            self.root,
            self.pipeline.submit,
            on_show=self.show_typing_bubble,
            on_hide=self.hide_typing_bubble,
            on_submit_hide=self.hide_input_window_only,
        )
        self.input_hotkey_manager = HotkeyManager(self.show_input)
        self.microphone_hotkey_manager = HotkeyManager(self.on_microphone_hotkey_press)
        self.preset_next_hotkey_manager = HotkeyManager(self.switch_next_preset)
        self.osc_toggle_hotkey_manager = HotkeyManager(self.toggle_listen_osc)
        self.realtime_toggle_hotkey_manager = HotkeyManager(self.toggle_realtime_translate)
        self.image_translate_hotkey_manager = HotkeyManager(self.on_image_translate_hotkey)
        self.control_window_hotkey_manager = HotkeyManager(self.show_control)
        self.preset_hotkey_managers = [HotkeyManager(lambda index=index: self.switch_preset(index)) for index in range(1, PRESET_COUNT + 1)]
        self.control_window = ControlWindow(
            self.root,
            self.config_manager,
            callbacks={
                "apply_preset": self._switch_preset,
                "save_preset": self.config_manager.save_current_to_preset,
                "toggle_realtime": self._toggle_realtime_translate,
                "toggle_osc": self._toggle_listen_osc,
                "show_input": self.show_input,
                "image_translate": self._handle_image_translate_hotkey,
                "set_overlay_alpha": lambda value: self.config_manager.patch_from_dict({"overlay_alpha": value}),
                "status": self._control_status,
            },
        )
        self.tray = TrayApp(self.config_manager, self.show_input, lambda: self._post(self.quit), self.show_control)
        self._typing_job: str | None = None
        self.mic_listener: MicrophoneListener | None = None
        self._mic_hotkey_pressed = False
        self._pending_mic_text = ""
        self._pending_mic_started_at: float | None = None
        self._pending_mic_job: str | None = None
        self._pending_mic_countdown_seconds = 0
        self._mic_press_cancelled_pipeline = False
        self._quitting = False
        # 图片翻译三态：idle | translating | showing；_img_seq 用于丢弃已取消/过期的后台结果
        self._img_state = "idle"
        self._img_seq = 0
        # 后台翻译线程 -> 主线程的结果队列（不跨线程调 root.after，避免弄挂 tkinter）
        self._img_queue: queue.Queue = queue.Queue()
        # 出图后自动消失计时器句柄（手动关闭/重新翻译时取消）
        self._img_hide_job: str | None = None

    def _speech_capture_status(self) -> dict:
        """返回双说话指示器状态：translate=实时翻译听到的声音（蓝），mic=自己麦克风 VAD（绿）。"""
        pipeline_status = PIPELINE.status()
        translate_speaking = bool(pipeline_status.get("running")) and bool(pipeline_status.get("speaking"))
        if not translate_speaking:
            legacy = output_capture_status()
            translate_speaking = bool(legacy.get("enabled")) and bool(legacy.get("speaking"))
        mic_speaking = False
        listener = self.mic_listener
        if isinstance(listener, VadMicListener):
            mic_status = listener.status()
            mic_speaking = bool(mic_status.get("enabled")) and bool(mic_status.get("speaking"))
        return {"translate_speaking": translate_speaking, "mic_speaking": mic_speaking}

    def start(self) -> None:
        config = self.config_manager.get()
        app = create_web_app(
            self.config_manager,
            self.error_handler,
            self.show_input,
            lambda: self._post(self.reload_runtime_mode),  # flask 线程触发 -> 投递到主线程执行
        )
        app.config["speech_indicator"] = self.speech_indicator
        start_web_server(app, config.web_host, config.web_port)
        self.speech_indicator.start()
        self.tray.start()
        self.reload_runtime_mode()
        webbrowser.open(f"http://{config.web_host}:{config.web_port}/")
        # 让 Ctrl+C / 终止信号能中断 tkinter mainloop：装信号处理 + 周期性唤醒解释器
        signal.signal(signal.SIGINT, self._handle_signal)
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            pass
        self._keep_alive()
        self._img_poll()
        self._call_poll()
        self._vr_watch()  # 尝试连接 SteamVR；连上则把显示切到 VR 并自动开启链接器
        self.root.mainloop()

    def _handle_signal(self, *_args) -> None:
        # tkinter mainloop 不会自行响应信号，这里把退出投递到主线程做干净关闭
        print("\n收到退出信号，正在关闭…")
        self.root.after(0, self.quit)

    def _keep_alive(self) -> None:
        # 周期性回到 Python 解释器，给信号处理函数执行机会（否则 Tk 会一直阻塞在 C 层收不到信号）
        if self._quitting:
            return
        self.root.after(200, self._keep_alive)

    def _post(self, fn) -> None:
        """线程安全地把一个可调用投递到 tkinter 主线程执行。任何非主线程（keyboard 热键回调、
        托盘、flask）想触发动作都必须经此，绝不能直接 root.after，否则会弄挂 Tk 事件循环。"""
        self._call_queue.put(fn)

    def _call_poll(self) -> None:
        # 主线程轮询：取出其它线程投递来的动作并在主线程执行（间隔短以保证热键响应及时）
        try:
            while True:
                fn = self._call_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    self.error_handler.report("主线程任务执行失败", exc)
        except queue.Empty:
            pass
        finally:
            if not self._quitting:
                self.root.after(20, self._call_poll)

    # ---------- SteamVR 动态接入/断开 ----------

    def _vr_watch(self) -> None:
        # 周期检查：未连上就尝试连接（SteamVR 没开就静默等待）；已连上则检测 SteamVR 是否已退出
        if self._quitting:
            return
        try:
            if self.vr_ui is None:
                self._try_attach_vr()
            elif self.vr_ui.steamvr_quit_requested():
                self._detach_vr()
        except Exception as exc:
            self.error_handler.report("SteamVR 连接检查失败", exc)
        self.root.after(2500, self._vr_watch)

    def _try_attach_vr(self) -> None:
        # 便宜的预检：SteamVR 没在跑时 isHmdPresent() 为假，直接返回，避免每次都去做完整的 openvr.init
        try:
            import openvr

            if not openvr.isHmdPresent():
                return
        except Exception:
            return

        from ui.vr_overlay import VROverlayUI

        try:
            vr = VROverlayUI(self.root, self._speech_capture_status, self.config_manager)
        except Exception:
            return  # SteamVR 未运行/未就绪，下次再试（静默，不打扰桌面使用）
        self.vr_ui = vr
        self.overlay_router.set_vr(vr)
        vr.start()
        vr.create_dashboard(
            self.config_manager,
            callbacks={
                "toggle_realtime": self._toggle_realtime_translate,
                "toggle_osc": self._toggle_listen_osc,
                "apply_preset": self._switch_preset,
                "show_input": self.show_input,
                "image_translate": self._handle_image_translate_hotkey,
                "bridge_status": self.bridge.status,
                "bridge_toggle": self.bridge.toggle,
            },
            status_provider=self._control_status,
        )
        self.bridge.start()  # 连上 SteamVR 后自动开启手柄链接器
        print(f"[{time.strftime('%H:%M:%S')}] 已连接 SteamVR：显示切换到 VR overlay，已启动手柄链接器")

    def _detach_vr(self) -> None:
        # SteamVR 退出：断开 VR overlay，显示回到桌面浮窗；链接器随 SteamVR 退出自行结束
        vr = self.vr_ui
        self.vr_ui = None
        self.overlay_router.set_vr(None)
        if vr is not None:
            try:
                vr.shutdown()
            except Exception:
                pass
        print(f"[{time.strftime('%H:%M:%S')}] SteamVR 已退出：显示回到桌面，等待下次连接")

    def show_input(self) -> None:
        self._post(self.toggle_input)

    def toggle_input(self) -> None:
        if self.input_window.is_visible():
            self.input_window.hide()
            return
        initial_text = self._consume_pending_microphone_text()
        self.input_window.show(initial_text)

    def show_typing_bubble(self) -> None:
        if self._typing_job is not None:
            return
        self._send_typing_state(True)
        self._typing_job = self.root.after(self.TYPING_REFRESH_MS, self._refresh_typing_bubble)

    def _refresh_typing_bubble(self) -> None:
        self._typing_job = None
        self.show_typing_bubble()

    def _send_typing_state(self, enabled: bool) -> None:
        try:
            VrcOscClient(self.config_manager.get()).set_typing(enabled)
        except Exception as exc:
            action = "发送" if enabled else "关闭"
            self.error_handler.report(f"{action}正在输入状态失败", exc)

    def hide_typing_bubble(self) -> None:
        if self._typing_job is not None:
            self.root.after_cancel(self._typing_job)
            self._typing_job = None
        self._send_typing_state(False)

    def hide_input_window_only(self) -> None:
        self.input_window.hide(notify=False)

    def reload_hotkey(self) -> None:
        try:
            config = self.config_manager.get()
            self.input_hotkey_manager.register(config.input_hotkey or config.hotkey)
        except Exception as exc:
            self.error_handler.report("输入框热键注册失败", exc)

    def reload_runtime_mode(self) -> None:
        config = self.config_manager.get()
        self.input_hotkey_manager.unregister()
        self.microphone_hotkey_manager.unregister()
        self.preset_next_hotkey_manager.unregister()
        self.osc_toggle_hotkey_manager.unregister()
        self.realtime_toggle_hotkey_manager.unregister()
        self.image_translate_hotkey_manager.unregister()
        self.control_window_hotkey_manager.unregister()
        for manager in self.preset_hotkey_managers:
            manager.unregister()
        self.stop_microphone_listener()
        self._clear_pending_microphone_text()
        self.start_microphone_listener(config)
        self.reload_hotkey()
        self.reload_microphone_hotkey()
        self.reload_preset_hotkeys()
        self.reload_osc_toggle_hotkey()
        self.reload_realtime_toggle_hotkey()
        self.reload_image_translate_hotkey()
        self.reload_control_window_hotkey()
        self.reload_realtime_pipeline()

    def reload_realtime_pipeline(self) -> None:
        """切换预设/配置后，若实时翻译正在运行则用新配置热重启，
        立即换上新的识别/翻译模型与参数（模型未变时命中缓存，几乎无停顿）。"""
        if not PIPELINE.status().get("running"):
            return
        config = self.config_manager.get()

        def _restart() -> None:
            try:
                PIPELINE.start(config, indicator=self.speech_indicator)
            except Exception as exc:
                self.error_handler.report("切换配置后重启实时翻译失败", exc)

        # 放后台线程，避免 stop() 等待旧线程退出时阻塞 tkinter 主线程
        threading.Thread(target=_restart, daemon=True).start()

    def switch_next_preset(self) -> None:
        self._post(self._switch_next_preset)

    def _switch_next_preset(self) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] 热键触发：切换下一个预设")
        config = self.config_manager.apply_next_preset()
        self.reload_runtime_mode()
        self._show_preset_switched(config.active_preset_index)

    def switch_preset(self, index: int) -> None:
        self._post(lambda: self._switch_preset(index))

    def _switch_preset(self, index: int) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] 热键触发：切换预设 {index}")
        config = self.config_manager.apply_preset(index)
        self.reload_runtime_mode()
        self._show_preset_switched(config.active_preset_index)

    def _show_preset_switched(self, index: int) -> None:
        config = self.config_manager.get()
        name = config.preset_names[index - 1]
        self.speech_indicator.show_toast(f"已切换到预设 {index}\n{name}")

    def reload_preset_hotkeys(self) -> None:
        for manager in self.preset_hotkey_managers:
            manager.unregister()
        self.preset_next_hotkey_manager.unregister()
        try:
            config = self.config_manager.get()
            next_hotkey = config.preset_next_hotkey.strip()
            if next_hotkey:
                self.preset_next_hotkey_manager.register(next_hotkey)
            for index, hotkey in enumerate(config.preset_hotkeys, start=1):
                hotkey = str(hotkey).strip()
                if hotkey:
                    self.preset_hotkey_managers[index - 1].register(hotkey)
        except Exception as exc:
            self.error_handler.report("预设切换热键注册失败", exc)

    def reload_osc_toggle_hotkey(self) -> None:
        try:
            config = self.config_manager.get()
            hotkey = config.speech_translate_osc_toggle_hotkey.strip()
            if hotkey:
                self.osc_toggle_hotkey_manager.register(hotkey)
        except Exception as exc:
            self.error_handler.report("聊天框翻译显示热键注册失败", exc)

    def toggle_listen_osc(self) -> None:
        self._post(self._toggle_listen_osc)

    def _toggle_listen_osc(self) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] 热键触发：聊天框翻译开关")
        # 管线运行中以其运行时开关为准（启动时可能被页面参数覆盖过），未运行时翻转配置值
        if PIPELINE.status().get("running"):
            enabled = PIPELINE.toggle_osc_enabled()
        else:
            enabled = not self.config_manager.get().speech_translate_osc_enabled
        self.config_manager.patch_from_dict({"speech_translate_osc_enabled": enabled})
        self.speech_indicator.show_toast(f"聊天框翻译显示：{'已开启' if enabled else '已关闭'}")

    def reload_realtime_toggle_hotkey(self) -> None:
        try:
            hotkey = self.config_manager.get().speech_translate_toggle_hotkey.strip()
            if hotkey:
                self.realtime_toggle_hotkey_manager.register(hotkey)
        except Exception as exc:
            self.error_handler.report("实时翻译开关热键注册失败", exc)

    def toggle_realtime_translate(self) -> None:
        self._post(self._toggle_realtime_translate)

    def _toggle_realtime_translate(self) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] 热键触发：实时翻译启停")
        # 启停均放后台线程：start() 内部会先 stop() 并 join 旧线程，可能阻塞 tkinter 主线程
        if PIPELINE.status().get("running"):
            self.speech_indicator.show_toast("实时翻译：已关闭")
            threading.Thread(target=self._stop_realtime_translate, daemon=True).start()
        else:
            config = self.config_manager.get()
            self.speech_indicator.show_toast("实时翻译：正在启动…")
            threading.Thread(target=lambda: self._start_realtime_translate(config), daemon=True).start()

    def _start_realtime_translate(self, config) -> None:
        try:
            PIPELINE.start(config, indicator=self.speech_indicator)
        except Exception as exc:
            self.error_handler.report("启动实时翻译失败", exc)

    def _stop_realtime_translate(self) -> None:
        try:
            PIPELINE.stop()
        except Exception as exc:
            self.error_handler.report("停止实时翻译失败", exc)

    # ---------- 图片翻译 ----------

    def reload_image_translate_hotkey(self) -> None:
        try:
            hotkey = self.config_manager.get().image_translate_hotkey.strip()
            if hotkey:
                self.image_translate_hotkey_manager.register(hotkey)
        except Exception as exc:
            self.error_handler.report("图片翻译热键注册失败", exc)

    def on_image_translate_hotkey(self) -> None:
        self._post(self._handle_image_translate_hotkey)

    def _handle_image_translate_hotkey(self) -> None:
        # 三态机：空闲->截图翻译；翻译中->直接取消（不提示）；出图后->关闭图片
        if self.vr_ui is None:
            self.status_overlay.show_hint("图片翻译仅在 VR 模式可用")
            return
        self._cancel_img_hide_job()  # 任何状态切换都先取消自动消失计时
        if self._img_state == "translating":
            self._img_seq += 1            # 让后台结果作废
            self._img_state = "idle"
            self.vr_ui.image_hide()
            return
        if self._img_state == "showing":
            self._img_state = "idle"
            self.vr_ui.image_hide()
            return
        self._img_state = "translating"
        self._img_seq += 1
        seq = self._img_seq
        self.vr_ui.image_show_loading()
        threading.Thread(target=lambda: self._run_image_translate(seq), daemon=True).start()

    def _run_image_translate(self, seq: int) -> None:
        # 后台线程：截图+翻译，结果只投递到队列，绝不在此跨线程碰 tkinter
        from services.image_translate import capture_vr_view, translate_image

        try:
            config = self.config_manager.get()
            shot = capture_vr_view(config)
            result = translate_image(shot, config)
        except Exception as exc:
            self._img_queue.put(("failed", seq, exc))
            return
        self._img_queue.put(("done", seq, result))

    def _img_poll(self) -> None:
        # 主线程轮询：取出后台翻译结果并应用（过期/已取消的丢弃）
        try:
            while True:
                kind, seq, payload = self._img_queue.get_nowait()
                if seq != self._img_seq or self.vr_ui is None:
                    continue  # 已被取消或被新请求取代
                if kind == "done":
                    self._img_state = "showing"
                    self.vr_ui.image_show_result(payload)
                    self._cancel_img_hide_job()
                    seconds = float(self.config_manager.get().image_translate_result_seconds)
                    if seconds > 0:
                        self._img_hide_job = self.root.after(int(seconds * 1000), self._img_auto_hide)
                else:
                    self._img_state = "idle"
                    self.vr_ui.image_hide()
                    self.error_handler.report("图片翻译失败", payload)
        except queue.Empty:
            pass
        finally:
            if not self._quitting:
                self.root.after(200, self._img_poll)

    def _cancel_img_hide_job(self) -> None:
        if self._img_hide_job is not None:
            self.root.after_cancel(self._img_hide_job)
            self._img_hide_job = None

    def _img_auto_hide(self) -> None:
        self._img_hide_job = None
        if self._img_state == "showing" and self.vr_ui is not None:
            self._img_state = "idle"
            self.vr_ui.image_hide()

    # ---------- 桌面控制面板 ----------

    def reload_control_window_hotkey(self) -> None:
        try:
            hotkey = self.config_manager.get().control_window_hotkey.strip()
            if hotkey:
                self.control_window_hotkey_manager.register(hotkey)
        except Exception as exc:
            self.error_handler.report("控制面板热键注册失败", exc)

    def show_control(self) -> None:
        self._post(self.control_window.show)

    def _control_status(self) -> dict:
        config = self.config_manager.get()
        return {
            "realtime_running": bool(PIPELINE.status().get("running")),
            "osc_enabled": bool(config.speech_translate_osc_enabled),
            "preset_index": config.active_preset_index,
            "preset_name": config.preset_names[config.active_preset_index - 1],
            "preset_names": list(config.preset_names),
            "overlay_alpha": float(config.overlay_alpha),
            "vr_mode": self.vr_ui is not None,
            "web_url": f"http://{config.web_host}:{config.web_port}/",
        }

    def reload_microphone_hotkey(self) -> None:
        try:
            config = self.config_manager.get()
            self.microphone_hotkey_manager.register(
                config.microphone_hotkey,
                release_callback=self.on_microphone_hotkey_release,
            )
        except Exception as exc:
            self.error_handler.report("麦克风热键注册失败", exc)

    def start_microphone_listener(self, config) -> None:
        if config.mic_vad_mode:
            self.mic_listener = VadMicListener(
                config,
                text_callback=self.on_microphone_text,
                error_callback=lambda exc: self.error_handler.report("麦克风 VAD 监听失败", exc),
            )
            self.mic_listener.start()
        else:
            self.mic_listener = MicrophoneListener(
                config,
                text_callback=self.on_microphone_text,
                error_callback=lambda exc: self.error_handler.report("麦克风监听失败", exc),
                finish_callback=self.on_microphone_capture_finish,
            )

    def on_microphone_hotkey_press(self) -> None:
        self._post(self._handle_microphone_hotkey_press)

    def on_microphone_hotkey_release(self) -> None:
        self._post(self._handle_microphone_hotkey_release)

    def _handle_microphone_hotkey_press(self) -> None:
        if self._mic_hotkey_pressed:
            return
        self._mic_hotkey_pressed = True
        if self.pipeline.cancel_before_audio():
            self._mic_press_cancelled_pipeline = True
            self.hide_typing_bubble()
            self._clear_pending_microphone_text()
            self.status_overlay.show_cancelled("已取消当前 TTS 操作", hide_after_ms=2200)
            return
        # 语音识别进行中再按热键 -> 取消本次识别（丢弃结果）
        listener = self.mic_listener
        if isinstance(listener, MicrophoneListener) and listener.request_cancel():
            self._mic_press_cancelled_pipeline = True  # 抑制随后配对的 release 动作
            self.hide_typing_bubble()
            self._clear_pending_microphone_text()
            self.status_overlay.show_cancelled("已取消语音识别", hide_after_ms=2000)
            return
        if self._pending_mic_text:
            text = self._pending_mic_text
            started_at = self._pending_mic_started_at
            self._clear_pending_microphone_text()
            self.show_typing_bubble()
            self.status_overlay.show_progress(0, self.pipeline.TOTAL_STEPS, f"确认发送：{text}")
            self.root.after(150, lambda: self.pipeline.submit(text, started_at))
            return
        if self.mic_listener is None:
            return
        if isinstance(self.mic_listener, VadMicListener):
            self.status_overlay.show_hint("VAD 持续监听中：先说话，识别出文字后再按热键发送")
            return
        if self.mic_listener.start_capture():
            self._pending_mic_started_at = time.perf_counter()
            self.show_typing_bubble()
            self.status_overlay.show_progress(0, self.pipeline.TOTAL_STEPS, "正在按键录音，松开后识别文字...")

    def _handle_microphone_hotkey_release(self) -> None:
        self._mic_hotkey_pressed = False
        if self._mic_press_cancelled_pipeline:
            self._mic_press_cancelled_pipeline = False
            return
        if self.pipeline.cancel_before_audio():
            self.hide_typing_bubble()
            self._clear_pending_microphone_text()
            self.status_overlay.show_cancelled("已取消当前 TTS 操作", hide_after_ms=2200)
            return
        if self.mic_listener is None or isinstance(self.mic_listener, VadMicListener):
            return
        self.mic_listener.stop_capture()
        self.status_overlay.show_progress(0, self.pipeline.TOTAL_STEPS, "录音结束，正在识别文字...")

    def stop_microphone_listener(self) -> None:
        if self.mic_listener is not None:
            self.mic_listener.stop()
            self.mic_listener = None
        self._mic_hotkey_pressed = False

    def on_microphone_text(self, text: str) -> None:
        def submit_later() -> None:
            self._set_pending_microphone_text(text)

        self._post(submit_later)

    def on_microphone_capture_finish(self, recognized: bool) -> None:
        if recognized:
            return
        def finish_later() -> None:
            self.hide_typing_bubble()
            self.status_overlay.show_warning("未识别到语音", hide_after_ms=1800)

        self._post(finish_later)

    def _set_pending_microphone_text(self, text: str) -> None:
        self._clear_pending_microphone_text()
        self._pending_mic_text = text
        self._pending_mic_countdown_seconds = max(1, int(self.config_manager.get().listen_confirm_timeout_seconds))
        self.show_typing_bubble()
        self._refresh_pending_microphone_countdown()

    def _refresh_pending_microphone_countdown(self) -> None:
        if not self._pending_mic_text:
            return
        self.status_overlay.show_progress(
            0,
            self.pipeline.TOTAL_STEPS,
            f"{self._pending_mic_text}\n\n{self._pending_mic_countdown_seconds}秒内再按热键发送",
        )
        if self._pending_mic_countdown_seconds <= 0:
            self._expire_pending_microphone_text()
            return
        self._pending_mic_countdown_seconds -= 1
        self._pending_mic_job = self.root.after(1000, self._refresh_pending_microphone_countdown)

    def _expire_pending_microphone_text(self) -> None:
        self._pending_mic_job = None
        self._pending_mic_text = ""
        self._pending_mic_started_at = None
        self._pending_mic_countdown_seconds = 0
        self.hide_typing_bubble()
        self.status_overlay.show_warning("语音识别结果已过期，未发送", hide_after_ms=1800)

    def _clear_pending_microphone_text(self) -> None:
        if self._pending_mic_job is not None:
            self.root.after_cancel(self._pending_mic_job)
            self._pending_mic_job = None
        self._pending_mic_text = ""
        self._pending_mic_started_at = None
        self._pending_mic_countdown_seconds = 0

    def _consume_pending_microphone_text(self) -> str:
        text = self._pending_mic_text
        if text:
            self._clear_pending_microphone_text()
            self.status_overlay.show_done("已填入语音识别结果", hide_after_ms=1200)
        return text

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.hide_typing_bubble()
        PIPELINE.stop()
        self.stop_microphone_listener()
        self._clear_pending_microphone_text()
        self.input_hotkey_manager.unregister()
        self.microphone_hotkey_manager.unregister()
        self.preset_next_hotkey_manager.unregister()
        self.osc_toggle_hotkey_manager.unregister()
        self.realtime_toggle_hotkey_manager.unregister()
        self.image_translate_hotkey_manager.unregister()
        self.control_window_hotkey_manager.unregister()
        self._cancel_img_hide_job()
        for manager in self.preset_hotkey_managers:
            manager.unregister()
        try:
            self.bridge.stop()  # 退出时一并关闭手柄链接器
        except Exception:
            pass
        if self.vr_ui is not None:
            self.vr_ui.shutdown()
        self.tray.stop()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    # 打包成单 exe 后，手柄链接器子进程通过本 exe 以 --run-bridge 再次启动自身来运行
    # （冻结环境里没有独立的 python.exe 和 .py 脚本可调）。源码运行时仍直接跑脚本，不走这里。
    if "--run-bridge" in sys.argv[1:]:
        sys.argv = [sys.argv[0]] + [arg for arg in sys.argv[1:] if arg != "--run-bridge"]
        from tools.vr_controller_bridge import main as bridge_main

        sys.exit(bridge_main())

    # 统一启动器：不再区分桌面/VR。默认先以桌面模式运行，并在后台尝试连接 SteamVR；
    # SteamVR 启动后会自动把显示切到 VR overlay 并开启手柄链接器（--vr 参数已废弃，保留兼容不报错）。
    try:
        Application().start()
    except Exception as exc:
        print(f"启动失败：{exc}")
        sys.exit(1)
