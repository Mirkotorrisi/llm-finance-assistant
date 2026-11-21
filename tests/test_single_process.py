"""The services refuse to run as one of several workers (their state lives in the process)."""

import pytest

from src.single_process import assert_single_process, configured_workers


def test_workers_are_read_from_uvicorn_flags_and_web_concurrency():
    assert configured_workers(["/x/bin/uvicorn", "app:app"], {}) == 1
    assert configured_workers(["/x/bin/uvicorn", "app:app", "--workers", "4"], {}) == 4
    assert configured_workers(["/x/bin/uvicorn", "--workers=3", "app:app"], {}) == 3
    assert configured_workers(["gunicorn", "-w", "2", "app:app"], {}) == 2
    assert configured_workers(["/x/bin/uvicorn", "app:app"], {"WEB_CONCURRENCY": "2"}) == 2
    assert configured_workers(["/x/bin/uvicorn", "app:app", "--workers", "x"], {"WEB_CONCURRENCY": ""}) == 1
    # other launchers (pytest, scripts) have flags of their own that are not worker counts
    assert configured_workers(["/x/bin/pytest", "-w", "4"], {}) == 1


def test_several_workers_refuse_to_start_unless_allowed():
    assert_single_process("api", ["/x/bin/uvicorn", "app:app"], {})
    with pytest.raises(RuntimeError, match="2 workers"):
        assert_single_process("api", ["/x/bin/uvicorn", "app:app", "--workers", "2"], {})
    assert_single_process("api", ["/x/bin/uvicorn", "app:app", "--workers", "2"], {"FINANCE_ALLOW_WORKERS": "1"})


