"""Start Premium Dating API + Telegram bot in one Render web service (free plan friendly)."""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys

import uvicorn


def main() -> None:
    env = os.environ.copy()

    # Start bot polling in background.
    bot_proc = subprocess.Popen([sys.executable, "bot/main.py"], env=env)

    def _cleanup(*_args) -> None:
        if bot_proc.poll() is None:
            bot_proc.terminate()
            try:
                bot_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                bot_proc.kill()

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    # Keep API in foreground for Render health checks and routing.
    uvicorn.run(
        "backend.api.swipe_handler:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
