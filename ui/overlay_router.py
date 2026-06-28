"""字幕/状态/指示器的统一出口。

pipeline、web、热键处理持有的都是本对象（稳定不变）的引用；当 SteamVR 接入/断开时，只需切换
内部 sink（桌面浮窗 <-> VR overlay），无需重新接线那些回调。所有调用都先经 post 投递到主线程再
转发，因此即便目标是非线程安全的桌面 tkinter 浮窗也安全。
"""
from __future__ import annotations


class OverlayRouter:
    def __init__(self, desktop_status, desktop_speech, post):
        self._status = desktop_status      # 桌面 StatusOverlay
        self._speech = desktop_speech      # 桌面 SpeechIndicator
        self._vr = None                    # VROverlayUI（接入 SteamVR 时）
        self._post = post                  # Application._post：投递到主线程执行

    def set_vr(self, vr) -> None:
        self._vr = vr

    # ---------- StatusOverlay API ----------

    def show_progress(self, step, total, message) -> None:
        self._post(lambda: (self._vr or self._status).show_progress(step, total, message))

    def show_warning(self, message, hide_after_ms=2200) -> None:
        self._post(lambda: (self._vr or self._status).show_warning(message, hide_after_ms))

    def show_done(self, message="完成", hide_after_ms=2200) -> None:
        self._post(lambda: (self._vr or self._status).show_done(message, hide_after_ms))

    def show_error(self, message="失败，请查看错误弹窗", hide_after_ms=5000) -> None:
        self._post(lambda: (self._vr or self._status).show_error(message, hide_after_ms))

    def show_cancelled(self, message="已取消", hide_after_ms=2200) -> None:
        self._post(lambda: (self._vr or self._status).show_cancelled(message, hide_after_ms))

    def show_hint(self, message, hide_after_ms=2200) -> None:
        self._post(lambda: (self._vr or self._status).show_hint(message, hide_after_ms))

    def hide(self) -> None:
        self._post(lambda: (self._vr or self._status).hide())

    # ---------- SpeechIndicator API ----------

    def show_text(self, text, seconds, alpha=0.78, speaker_label="", speaker_color="") -> None:
        self._post(lambda: (self._vr or self._speech).show_text(text, seconds, alpha, speaker_label, speaker_color))

    def show_toast(self, text, seconds=2.2) -> None:
        self._post(lambda: (self._vr or self._speech).show_toast(text, seconds))

    def start(self) -> None:
        # 桌面指示器的刷新循环常驻；VR overlay 的 tick 在接入时单独 start
        self._speech.start()
