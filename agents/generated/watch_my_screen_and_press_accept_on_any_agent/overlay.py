"""
Tiny always-on-top status overlay.

Shows a small floating widget in the top-right corner so you can see at a
glance that the Codex watcher is active.  Runs in a background thread so it
does not block the main workflow loop.

Usage (called automatically by launch.py):
    from overlay import StatusOverlay
    overlay = StatusOverlay()
    overlay.start()
    overlay.set_status("watching")
    overlay.set_status("accepted 3")
    overlay.stop()
"""

import threading
import time


class StatusOverlay:
    """Floating tkinter label — decoupled from the watcher loop."""

    def __init__(self) -> None:
        self._status = "starting…"
        self._accepted = 0
        self._running = False
        self._thread: threading.Thread | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_tk, daemon=True)
        self._thread.start()
        time.sleep(0.3)  # give tkinter a moment to draw

    def stop(self) -> None:
        self._running = False

    def set_watching(self) -> None:
        self._status = "watching"

    def set_accepted(self, count: int) -> None:
        self._accepted = count
        self._status = f"accepted {count}"

    def set_done(self) -> None:
        self._status = "done"

    # ── internal ──────────────────────────────────────────────────────────────

    def _run_tk(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title("")
            root.overrideredirect(True)          # no title bar
            root.attributes("-topmost", True)    # always on top
            root.attributes("-alpha", 0.85)

            # Position: top-right corner
            sw = root.winfo_screenwidth()
            root.geometry(f"160x54+{sw - 174}+12")
            root.configure(bg="#1a1a2e")

            icon_lbl = tk.Label(
                root, text="👁", font=("Helvetica", 18),
                bg="#1a1a2e", fg="#e94560",
            )
            icon_lbl.pack(side="left", padx=(8, 4))

            text_var = tk.StringVar(value="Codex Watcher\nstarting…")
            text_lbl = tk.Label(
                root, textvariable=text_var,
                font=("Helvetica", 9), justify="left",
                bg="#1a1a2e", fg="#ffffff",
            )
            text_lbl.pack(side="left", fill="both", expand=True)

            def _tick() -> None:
                if not self._running:
                    root.destroy()
                    return
                text_var.set(f"Codex Watcher\n{self._status}")
                root.after(500, _tick)

            root.after(500, _tick)
            root.mainloop()

        except Exception:
            # If tkinter is unavailable just silently skip the overlay
            pass
