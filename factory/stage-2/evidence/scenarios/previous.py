"""Start the previous stage's real service (stage 1 as shipped) so upgrade scenarios can export real state.

PREVIOUS_BASE_URL overrides this with an already running stage-1 service.
"""
import os
import subprocess
import tempfile
import time

import httpx

RESULT = "/Users/whom/dark-factory/band-work/final/result"
STAGE1_COMMIT = "a147f3b56e6df534d4298bf8ab9eed3a8ab07fe0"
TAG = "pocketful-holdout-stage1-previous"
PORT = 18291


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class Previous:
    def __init__(self):
        self.container = None
        self.base = os.environ.get("PREVIOUS_BASE_URL")

    def start(self):
        if self.base:
            return self.base.rstrip("/")
        with tempfile.TemporaryDirectory() as scratch:
            archive = os.path.join(scratch, "s1.tar")
            out = _run(["git", "-C", RESULT, "archive", "-o", archive, STAGE1_COMMIT, "stage-1"], timeout=60)
            assert out.returncode == 0, out.stderr
            assert _run(["tar", "-xf", archive, "-C", scratch], timeout=60).returncode == 0
            build = _run(["docker", "build", "-q", "-t", TAG, os.path.join(scratch, "stage-1")], timeout=600)
            assert build.returncode == 0, build.stdout + build.stderr
        _run(["docker", "rm", "-f", "pocketful-holdout-prev"], timeout=60)
        started = _run(["docker", "run", "-d", "--rm", "--name", "pocketful-holdout-prev", "-p",
                        f"127.0.0.1:{PORT}:8080", "-e", "PORT=8080", TAG], timeout=60)
        assert started.returncode == 0, started.stderr
        self.container = started.stdout.strip()
        base = f"http://127.0.0.1:{PORT}"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if httpx.get(base + "/health", timeout=2).status_code == 200:
                    self.base = base
                    return base
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise AssertionError("previous-stage service never became healthy")

    def stop(self):
        if self.container:
            _run(["docker", "stop", "-t", "2", self.container], timeout=60)
