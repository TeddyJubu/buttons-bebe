"""Light frontend checks (TESTING-STRATEGY §2.4).

The console is static HTML+JS built by another agent and may not exist yet — the
tests degrade to the JSON stub the server serves in that case. Visual/interaction
testing is manual for MVP (Playwright is a Sprint-2 item).
"""
import pathlib
import shutil
import subprocess

import pytest

CONSOLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "console"


def test_root_serves_something(env):
    r = env.client.get("/")
    assert r.status_code in (200, 404)  # 404 only if console dir exists but has no index.html
    if not CONSOLE_DIR.is_dir():
        # server serves the JSON stub advertising the API
        body = r.json()
        assert body["service"] == "fable"
        assert body["api"] == "/fable/api"


@pytest.mark.skipif(not CONSOLE_DIR.is_dir(), reason="console not built yet")
def test_console_index_has_app_root(env):
    r = env.client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()


@pytest.mark.skipif(shutil.which("node") is None or not CONSOLE_DIR.is_dir(),
                    reason="node not available or console not built")
def test_console_js_syntax_ok():
    js_files = list(CONSOLE_DIR.rglob("*.js"))
    if not js_files:
        pytest.skip("no JS files in console")
    for js in js_files:
        res = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
        assert res.returncode == 0, f"syntax error in {js}: {res.stderr}"
