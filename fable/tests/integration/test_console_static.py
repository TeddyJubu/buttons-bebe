"""Static regression guards for the Fable console (Stream C — Sprint 2).

Crude but CI-able source-level checks that lock in the P0/P1 fixes from
DESIGN-CRITIQUE.md so they can't silently regress:

  * captureDraft() is called in the re-rendering handlers (close / snooze /
    addtag / data-snooze) — otherwise in-progress draft edits are lost (bug B1).
  * customer cards carry keyboard affordances (role/tabindex) — bug B2.
  * the type scale has no stray 13.5px half-pixel size (item 11).

These read the built console source directly; they don't need the server.
"""
import pathlib
import re

import pytest

CONSOLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "console"
APP_JS = CONSOLE_DIR / "app.js"
STYLE_CSS = CONSOLE_DIR / "style.css"

pytestmark = pytest.mark.skipif(
    not APP_JS.is_file() or not STYLE_CSS.is_file(),
    reason="console not built yet",
)


def _app_js():
    return APP_JS.read_text(encoding="utf-8")


def _style_css():
    return STYLE_CSS.read_text(encoding="utf-8")


@pytest.mark.parametrize("handler_id", ["close", "snoozebtn", "addtag"])
def test_capture_draft_in_rerender_handlers(handler_id):
    """Each handler that re-renders must captureDraft() first (bug B1)."""
    src = _app_js()
    # within a short window after el("<id>") the handler must call captureDraft()
    pat = re.compile(r'el\("' + re.escape(handler_id) + r'"\)[\s\S]{0,200}?captureDraft\(\)')
    assert pat.search(src), f'captureDraft() not found in the {handler_id!r} handler'


def test_capture_draft_in_snooze_option_handler():
    """The per-option snooze buttons also re-render and must captureDraft() first."""
    src = _app_js()
    pat = re.compile(r'data-snooze[\s\S]{0,200}?captureDraft\(\)')
    assert pat.search(src), "captureDraft() not found in the data-snooze option handler"


def test_customer_card_is_keyboard_operable():
    """Customer cards need role=button + tabindex like ticket cards (bug B2)."""
    src = _app_js()
    pat = re.compile(r'class="custcard"[\s\S]{0,120}tabindex="0"')
    assert pat.search(src), 'custcard markup is missing tabindex="0"'
    assert 'onkeydown=function(e){ if(e.key==="Enter"||e.key===" ")' in src \
        or re.search(r'\[data-cust\][\s\S]{0,200}onkeydown', src), \
        "customer cards have no Enter/Space keydown handler"


def test_no_half_pixel_type_size():
    """The type scale should not reintroduce 13.5px (item 11 / DESIGN-SYSTEM §2)."""
    assert "13.5px" not in _style_css(), "13.5px half-pixel size crept back into style.css"


def test_channel_icons_are_svg_not_emoji():
    """Channel icons are inline SVG now, not the old emoji (item 10)."""
    src = _app_js()
    assert "CHAN_SVG" in src, "expected inline SVG channel icons (CHAN_SVG map)"
    for emoji in ("✉", "\U0001f4ac", "\U0001f7e2"):  # ✉ 💬 🟢
        assert emoji not in src, f"emoji channel icon {emoji!r} still present in app.js"


def test_undo_send_window_exists():
    """Send goes through a cancellable undo window before the actual POST (item 7)."""
    src = _app_js()
    assert "scheduleSend" in src and "commitSend" in src and "flushPendingSend" in src, \
        "undo-send machinery (scheduleSend/commitSend/flushPendingSend) missing"
