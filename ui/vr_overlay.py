"""SteamVR overlay 模式：把所有显示类浮窗渲染成纹理，推送到一块 HMD 锁定的 overlay。

VROverlayUI 同时实现桌面版 SpeechIndicator 与 StatusOverlay 的全部公开方法，
因此 main.py 在 --vr 模式下可直接用它替换两者，调用方无感知。所有状态修改都通过
tkinter 的 root.after 投递到主线程，与桌面版一致的单线程模型，避免 ctypes 调用竞争。
"""
from __future__ import annotations

import ctypes
import time
import tkinter as tk

from core.config import ConfigManager
from core.errors import AppError
from ui import vr_renderer as renderer

_TICK_MS = 100              # overlay 刷新 ~10fps，足够字幕与呼吸动画
_SUBTITLE_LIMIT = 6         # 同时显示的字幕条上限，超出丢最旧
_FADE_SECONDS = 0.8         # 末尾渐隐时长


class VRSession:
    """封装 pyopenvr：创建一块 HMD 相对定位的 overlay，并推送 RGBA 纹理。"""

    def __init__(self, width_m: float = 1.1, distance_m: float = 1.4, vertical_m: float = -0.55):
        try:
            import openvr
        except Exception as exc:  # 理论上不会发生（requirements 已含），保险起见
            raise AppError("VR 模式失败：未安装 openvr，请先 pip install openvr") from exc

        self._openvr = openvr
        try:
            openvr.init(openvr.VRApplication_Overlay)
        except Exception as exc:
            raise AppError(
                "VR 模式失败：无法连接 SteamVR。请先启动 SteamVR 再运行本程序。\n"
                f"（{exc}）"
            ) from exc

        self._overlay = openvr.IVROverlay()
        self._handle = self._overlay.createOverlay("vrc.voicebridge.overlay", "VRC VoiceBridge")
        self._overlay.setOverlayWidthInMeters(self._handle, width_m)
        self._overlay.setOverlayAlpha(self._handle, 1.0)

        # 相对 HMD 定位：正前方 distance_m、略微下移 vertical_m，跟随视野
        matrix = openvr.HmdMatrix34_t()
        matrix.m[0][0], matrix.m[0][1], matrix.m[0][2], matrix.m[0][3] = 1.0, 0.0, 0.0, 0.0
        matrix.m[1][0], matrix.m[1][1], matrix.m[1][2], matrix.m[1][3] = 0.0, 1.0, 0.0, vertical_m
        matrix.m[2][0], matrix.m[2][1], matrix.m[2][2], matrix.m[2][3] = 0.0, 0.0, 1.0, -distance_m
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._handle, openvr.k_unTrackedDeviceIndex_Hmd, matrix
        )
        self._shown = False
        self._last_data: bytes | None = None

    def submit(self, image) -> None:
        data = image.tobytes()  # PIL RGBA -> R,G,B,A 字节，与 setOverlayRaw 期望一致
        # 内容未变则跳过纹理重传：每 100ms 重传同一帧会让 SteamVR overlay 持续闪烁
        if data != self._last_data:
            buffer = (ctypes.c_char * len(data)).from_buffer_copy(data)
            self._overlay.setOverlayRaw(self._handle, buffer, image.width, image.height, 4)
            self._last_data = data
        if not self._shown:
            self._overlay.showOverlay(self._handle)
            self._shown = True

    def hide(self) -> None:
        if self._shown:
            try:
                self._overlay.hideOverlay(self._handle)
            except Exception:
                pass
            self._shown = False
        self._last_data = None  # 重新显示时强制重传一次纹理

    def poll_events(self) -> None:
        # 排空 OpenVR overlay 事件队列。SteamVR 会持续往队列推事件（焦点进出、仪表盘开关、
        # 鼠标、系统事件等）；若从不消费，队列无限堆积，跑一段时间后 overlay 会被卡住、不再刷新——
        # 这正是“只有 VR 模式、用一阵才卡死”的根因。每帧排空即可。
        event = self._openvr.VREvent_t()
        try:
            while self._overlay.pollNextOverlayEvent(self._handle, event):
                pass
        except Exception:
            pass

    def shutdown(self) -> None:
        try:
            self._openvr.shutdown()
        except Exception:
            pass


class VROverlayUI:
    """SpeechIndicator + StatusOverlay 的 VR 替身。公开方法签名与桌面版保持一致。"""

    def __init__(self, root: tk.Tk, status_provider, config_manager: ConfigManager):
        self.root = root
        self.status_provider = status_provider
        self.config_manager = config_manager
        self._session = VRSession()
        self._subtitles: list[dict] = []
        self._toast: dict | None = None
        self._status: dict | None = None
        self._phase_step = 0
        self._phase_counter = 0
        self._tick_job: str | None = None
        self._last_empty = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._tick_job is None:
            self._tick()

    def stop(self) -> None:
        if self._tick_job is not None:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        self._session.hide()

    def shutdown(self) -> None:
        self.stop()
        self._session.shutdown()

    # ---------- SpeechIndicator API ----------

    def show_text(self, text, seconds, alpha=0.78, speaker_label="", speaker_color="") -> None:
        text = str(text).strip()
        if not text:
            return
        color = renderer.hex_to_rgb(speaker_color) if speaker_color else renderer.BUBBLE_BORDER
        item = {
            "text": text,
            "expire_at": time.monotonic() + max(1.0, float(seconds)),
            "base_alpha": min(max(float(alpha), 0.1), 1.0),
            "speaker_label": speaker_label,
            "speaker_color": color,
        }
        self.root.after(0, lambda: self._add_subtitle(item))

    def show_toast(self, text, seconds=2.2) -> None:
        text = str(text).strip()
        if not text:
            return
        toast = {"text": text, "expire_at": time.monotonic() + max(0.5, float(seconds)), "base_alpha": 0.95}
        self.root.after(0, lambda: setattr(self, "_toast", toast))

    # ---------- StatusOverlay API ----------

    def show_progress(self, step, total, message) -> None:
        self._set_status("progress", f"{step}/{total}", message, None)

    def show_warning(self, message, hide_after_ms=2200) -> None:
        self._set_status("warning", "超时", message, hide_after_ms)

    def show_done(self, message="完成", hide_after_ms=2200) -> None:
        self._set_status("done", "完成", message, hide_after_ms)

    def show_error(self, message="失败，请查看错误弹窗", hide_after_ms=5000) -> None:
        self._set_status("error", "失败", message, hide_after_ms)

    def show_cancelled(self, message="已取消", hide_after_ms=2200) -> None:
        self._set_status("cancelled", "取消", message, hide_after_ms)

    def show_hint(self, message, hide_after_ms=2200) -> None:
        self._set_status("progress", "提示", message, hide_after_ms)

    def hide(self) -> None:
        self.root.after(0, lambda: setattr(self, "_status", None))

    # ---------- 内部 ----------

    def _set_status(self, state, badge, message, hide_after_ms) -> None:
        expire_at = None if hide_after_ms is None else time.monotonic() + hide_after_ms / 1000.0
        status = {"state": state, "badge": badge, "message": str(message), "expire_at": expire_at}
        self.root.after(0, lambda: setattr(self, "_status", status))

    def _add_subtitle(self, item: dict) -> None:
        self._subtitles.append(item)
        if len(self._subtitles) > _SUBTITLE_LIMIT:
            self._subtitles = self._subtitles[-_SUBTITLE_LIMIT:]

    def _tick(self) -> None:
        # 任何一次渲染/overlay 推送异常都不能中断刷新循环，否则浮窗会卡死在最后一帧、不再更新
        try:
            self._tick_body()
        except Exception:
            pass
        finally:
            self._tick_job = self.root.after(_TICK_MS, self._tick)

    def _tick_body(self) -> None:
        now = time.monotonic()

        # 每帧排空 OpenVR 事件队列，避免队列堆积导致 overlay 卡死（见 VRSession.poll_events）
        self._session.poll_events()

        # 过期清理 + 末尾渐隐
        kept = []
        for item in self._subtitles:
            remaining = item["expire_at"] - now
            if remaining <= 0:
                continue
            fade = remaining / _FADE_SECONDS if remaining < _FADE_SECONDS else 1.0
            item["alpha"] = item["base_alpha"] * fade
            kept.append(item)
        self._subtitles = kept

        if self._toast is not None:
            remaining = self._toast["expire_at"] - now
            if remaining <= 0:
                self._toast = None
            else:
                fade = remaining / _FADE_SECONDS if remaining < _FADE_SECONDS else 1.0
                self._toast["alpha"] = self._toast["base_alpha"] * fade

        if self._status is not None and self._status["expire_at"] is not None and now >= self._status["expire_at"]:
            self._status = None

        try:
            raw = self.status_provider() or {}
            indicators = {
                "translate": bool(raw.get("translate_speaking")),
                "mic": bool(raw.get("mic_speaking")),
            }
        except Exception:
            indicators = {"translate": False, "mic": False}

        status_payload = None
        if self._status is not None:
            base = min(max(self.config_manager.get().overlay_alpha, 0.1), 1.0)
            status_payload = {**self._status, "alpha": base}

        # 呼吸动画放慢到每 3 帧前进一步：相邻多帧画面相同，配合 submit 去重减少纹理重传，缓解转头闪烁
        self._phase_counter = (self._phase_counter + 1) % 3
        if self._phase_counter == 0:
            self._phase_step = (self._phase_step + 1) % 16
        state = {
            "subtitles": self._subtitles,
            "indicators": indicators,
            "status": status_payload,
            "toast": self._toast,
            "phase": self._phase_step / 16.0,
        }

        image = renderer.render_composite(state)
        if image is None:
            if not self._last_empty:
                self._session.hide()
                self._last_empty = True
        else:
            self._session.submit(image)
            self._last_empty = False
