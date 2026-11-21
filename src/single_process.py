"""Refuse to start as one of several worker processes.

The services keep state in the process: the API runs the price scheduler (a 25-requests-a-day budget that N workers
would spend N times), the gateway holds chat sessions, batch jobs and pending confirmations in memory. With
``uvicorn --workers N`` (or ``WEB_CONCURRENCY``) requests land on a random worker and that state is simply missing
there, so the second worker refuses to start instead of serving half the time. Scale with one process per service.
``FINANCE_ALLOW_WORKERS=1`` turns the check off for whoever moves that state out of the process.
"""

import os
import sys


def configured_workers(argv: list = None, env: dict = None) -> int:
    """Worker count a uvicorn command line / environment asks for (1 when none)."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    count = 1
    try:
        count = max(count, int(env.get("WEB_CONCURRENCY", "1") or 1))
    except ValueError:
        pass
    launcher = os.path.basename(str(argv[0])) if argv else ""
    if not any(name in launcher for name in ("uvicorn", "gunicorn", "hypercorn")):
        return count  # pytest, scripts and other launchers have flags of their own
    for i, arg in enumerate(argv):
        value = None
        if arg in ("--workers", "-w") and i + 1 < len(argv):
            value = argv[i + 1]
        elif arg.startswith("--workers="):
            value = arg.split("=", 1)[1]
        if value is not None:
            try:
                count = max(count, int(value))
            except ValueError:
                pass
    return count


def assert_single_process(service: str, argv: list = None, env: dict = None) -> None:
    env = os.environ if env is None else env
    workers = configured_workers(argv, env)
    if workers > 1 and env.get("FINANCE_ALLOW_WORKERS") != "1":
        raise RuntimeError(
            f"{service}: {workers} workers requested, but this service keeps state in the process and must run as "
            "one. Start it without --workers / WEB_CONCURRENCY (FINANCE_ALLOW_WORKERS=1 overrides)."
        )
