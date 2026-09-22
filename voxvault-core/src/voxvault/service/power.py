"""Hearing the machine say it is about to sleep.

Windows gives roughly two seconds to react to a suspend notice. A full
finalization includes lossless compression of the whole recording, which does
not remotely fit, so attempting it would mean losing the meeting instead of
saving it.

What fits is the **durable minimum**: stop capturing, force the pending audio
out, close the files, write the metadata. Everything after that is derived work
the next resume -- or the next start -- can redo.

The notification arrives through ``PowerRegisterSuspendResumeNotification``,
which delivers to a callback and needs no window. A console process or a
service has no message loop to receive ``WM_POWERBROADCAST`` on, and building
a hidden window to get one would be a lot of machinery for a callback the
operating system already offers.
"""

from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Callable
from ctypes import wintypes

#: Values of the notification's event code.
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

DEVICE_NOTIFY_CALLBACK = 0x00000002

#: The specification's ceiling for the durable minimum, and the reason it is
#: the only thing attempted here.
DURABLE_MINIMUM_MS = 1000


class _SubscribeParams(ctypes.Structure):
    _fields_ = [
        ("Callback", ctypes.WINFUNCTYPE(
            wintypes.ULONG, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
        )),
        ("Context", ctypes.c_void_p),
    ]


class PowerWatcher:
    """Calls back when the machine is about to suspend, and when it resumes.

    Registration failing is not fatal: the recorder still works, it simply
    will not get the warning. That is reported rather than raised, because a
    machine that cannot deliver power notifications is still a machine
    somebody wants to record a meeting on.
    """

    def __init__(
        self,
        *,
        on_suspend: Callable[[], None],
        on_resume: Callable[[], None] | None = None,
    ) -> None:
        self.on_suspend = on_suspend
        self.on_resume = on_resume or (lambda: None)
        self.registered = False
        self.failure = ""
        self._handle = ctypes.c_void_p()
        self._params: _SubscribeParams | None = None
        self._callback = None
        self._lock = threading.Lock()
        self._suspended = False

    # -- registration --------------------------------------------------

    def start(self) -> bool:
        if os.name != "nt":
            self.failure = "notificacao de energia so existe no Windows"
            return False

        powrprof = ctypes.windll.powrprof
        register = getattr(
            powrprof, "PowerRegisterSuspendResumeNotification", None
        )
        if register is None:
            self.failure = (
                "esta versao do Windows nao expoe "
                "PowerRegisterSuspendResumeNotification"
            )
            return False

        prototype = ctypes.WINFUNCTYPE(
            wintypes.ULONG, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p
        )
        # Held on the instance: the operating system keeps the pointer, and a
        # callback collected by Python would crash the process on suspend.
        self._callback = prototype(self._dispatch)
        self._params = _SubscribeParams(Callback=self._callback, Context=None)

        register.restype = wintypes.DWORD
        result = register(
            wintypes.DWORD(DEVICE_NOTIFY_CALLBACK),
            ctypes.byref(self._params),
            ctypes.byref(self._handle),
        )
        if result != 0:
            self.failure = f"registro recusado pelo sistema (codigo {result})"
            return False
        self.registered = True
        return True

    def stop(self) -> None:
        if not self.registered:
            return
        unregister = getattr(
            ctypes.windll.powrprof, "PowerUnregisterSuspendResumeNotification", None
        )
        if unregister is not None:
            unregister(self._handle)
        self.registered = False

    # -- the callback --------------------------------------------------

    def _dispatch(self, _context, event_type, _setting) -> int:
        """Runs on an operating-system thread, with the clock already ticking."""
        try:
            if event_type == PBT_APMSUSPEND:
                with self._lock:
                    if self._suspended:
                        return 0
                    self._suspended = True
                self.on_suspend()
            elif event_type in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                with self._lock:
                    if not self._suspended:
                        return 0
                    self._suspended = False
                self.on_resume()
        except Exception:
            # Raising back into the operating system would take the process
            # down at the worst possible moment -- mid-suspend, with a
            # recording open.
            pass
        return 0

    @property
    def suspended(self) -> bool:
        return self._suspended
