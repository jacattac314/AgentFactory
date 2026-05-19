"""
launch.py — entry point for the Codex screen-watcher agent.

Wires up real pyautogui + Claude vision adapters, starts the status overlay,
and runs the workflow loop.

Usage:
    python launch.py
    python launch.py --interval 2 --max 500
    python launch.py --dry-run
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch screen and accept Codex pop-ups automatically."
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan only, do not act.")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Seconds between screenshots (default 3).")
    parser.add_argument("--max", type=int, default=300,
                        help="Maximum polling cycles (default 300 ≈ 15 min).")
    args = parser.parse_args()

    if args.dry_run:
        from workflow import run
        result = run({"dry_run": True})
        print("\n--- Dry Run ---")
        for action in result.get("planned_actions", []):
            print(f"  • {action}")
        print(f"  {result['message']}")
        return

    # Start the status overlay (best-effort — skipped if tkinter unavailable)
    from overlay import StatusOverlay
    overlay = StatusOverlay()
    overlay.start()

    try:
        from workflow import run, _ScreenAdapter, _MouseAdapter, _VisionAdapter

        screen = _ScreenAdapter()
        mouse = _MouseAdapter()
        vision = _VisionAdapter()

        # Monkey-patch mouse adapter to keep overlay in sync
        _original_click = mouse.click
        def _tracked_click(x: int, y: int) -> None:
            _original_click(x, y)
            overlay.set_accepted(getattr(mouse, "_count", 0) + 1)
            mouse._count = getattr(mouse, "_count", 0) + 1
        mouse.click = _tracked_click

        overlay.set_watching()

        result = run({
            "dry_run": False,
            "tools": {"screen": screen, "mouse": mouse, "vision": vision},
            "config": {
                "poll_interval_seconds": args.interval,
                "max_iterations": args.max,
            },
        })

        overlay.set_done()

        print(f"\n--- Done ---")
        print(f"  Status   : {result['status']}")
        print(f"  Accepted : {result['accepted_count']}")
        print(f"  Cycles   : {result['iterations']}")

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        overlay.stop()


if __name__ == "__main__":
    main()
