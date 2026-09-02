"""
A Streamlit stand-in, so the pages can be executed by a test.

WHY THIS IS THE HIGHEST-VALUE TEST IN THE REPOSITORY
----------------------------------------------------
Coverage said 773 statements had never been executed by anything, and the worst
of them was `farmer_app/record.py` at zero - the data-entry page, which is the
only route by which any gate in this platform is ever unlocked. Nothing had
ever run it.

The consequence is not theoretical. Two defects shipped that this would have
caught in a second:

  * `_render_map` was CALLED and never defined. A name used only inside a
    function body is not resolved until that body runs, so the module imported
    cleanly, every test passed, and the page died with NameError in front of
    the first person who drew a field and reloaded.

  * A whole page of the sidebar rendered as `page_units` and `page_backup`,
    because `t()` returns its key when a label is missing and nothing ever
    looked at the output.

Streamlit itself cannot be driven from pytest without a browser and a server.
So this records calls instead of drawing them, and returns plausible values, so
a page function runs top to bottom and a test can assert what it tried to show.

WHAT THIS DOES NOT TEST
-----------------------
Layout, styling, whether a widget is reachable with a thumb, or whether the
page is comprehensible. It executes the code and captures the text. Everything
visual still needs a browser and an eye, and the six defects the live run found
are a standing reminder that a green suite is not a working product.
"""

from __future__ import annotations

import contextlib
import sys
from types import ModuleType, SimpleNamespace


class Recorder:
    """Everything a page tried to render, in order."""

    def __init__(self):
        self.calls = []          # (method, args, kwargs)
        self.text = []           # every string that would have reached a reader
        # st.warning is a DELIBERATE message - the open-deployment notice, the
        # unmeasured caveat - and counting it as a failure would make every
        # honest refusal in this platform fail its own test. Only st.error and
        # st.exception mean something went wrong.
        self.errors = []
        self.warnings = []
        self.stopped = False

    def note(self, method, args, kwargs):
        self.calls.append((method, args, kwargs))
        for a in args:
            if isinstance(a, str):
                self.text.append(a)
        if method in ("error", "exception"):
            self.errors += [str(a) for a in args]
        elif method == "warning":
            self.warnings += [a for a in args if isinstance(a, str)]

    def said(self, needle: str) -> bool:
        return any(needle in t for t in self.text)

    def methods(self) -> set:
        return {m for m, _a, _k in self.calls}

    def of(self, method) -> list:
        return [(a, k) for m, a, k in self.calls if m == method]


class _Stop(Exception):
    """What st.stop() raises, so a page ends where it means to."""


class _Widget:
    """A widget that records itself and returns something usable.

    Returns are deliberately boring - empty text, nothing selected, buttons not
    pressed - because the default path is the one a reader sees first and the
    one most likely to be broken.
    """

    def __init__(self, rec, name, parent=None):
        self._rec, self._name, self._parent = rec, name, parent

    def __call__(self, *args, **kwargs):
        self._rec.note(self._name, args, kwargs)
        n = self._name
        if n in ("button", "form_submit_button", "toggle", "checkbox",
                 "download_button", "link_button"):
            return False
        if n in ("text_input", "text_area"):
            return kwargs.get("value", args[1] if len(args) > 1 else "") or ""
        if n in ("number_input", "slider"):
            return kwargs.get("value", 0) or 0
        if n in ("selectbox", "radio"):
            opts = kwargs.get("options", args[1] if len(args) > 1 else [])
            opts = list(opts or [])
            idx = kwargs.get("index", 0) or 0
            return opts[idx] if opts and idx < len(opts) else None
        if n == "multiselect":
            return []
        if n in ("date_input", "time_input", "file_uploader", "camera_input"):
            return None
        if n == "columns":
            k = args[0] if args else 2
            k = k if isinstance(k, int) else len(k)
            return [_Container(self._rec) for _ in range(k)]
        if n == "tabs":
            return [_Container(self._rec) for _ in (args[0] if args else [1])]
        if n in ("expander", "container", "form", "spinner", "status",
                 "popover", "empty", "sidebar"):
            return _Container(self._rec)
        if n == "data_editor":
            return args[0] if args else []
        if n == "stop":
            self._rec.stopped = True
            raise _Stop()
        if n == "rerun":
            raise _Stop()
        return None

    def __getattr__(self, item):
        return _Widget(self._rec, f"{self._name}.{item}", self)


class _Container(_Widget):
    """A column, tab, expander or form: a widget that is also a context
    manager and carries the same surface."""

    def __init__(self, rec, name="container"):
        super().__init__(rec, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, item):
        return _Widget(self._rec, item)


class _SessionState(dict):
    """Streamlit's session_state is a dict that also does attributes."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


def make(state=None) -> tuple:
    """Build a stub module and its recorder."""
    rec = Recorder()
    mod = ModuleType("streamlit")

    for name in ("write", "markdown", "caption", "text", "code", "title",
                 "header", "subheader", "divider", "json", "table",
                 "dataframe", "metric", "progress", "success", "info",
                 "warning", "error", "exception", "image", "map",
                 "line_chart", "bar_chart", "area_chart", "pyplot",
                 "plotly_chart", "altair_chart", "set_page_config", "stop",
                 "rerun", "button", "form_submit_button", "download_button",
                 "link_button", "toggle", "checkbox", "text_input",
                 "text_area", "number_input", "slider", "selectbox", "radio",
                 "multiselect", "date_input", "time_input", "file_uploader",
                 "camera_input", "columns", "tabs", "expander", "container",
                 "form", "spinner", "status", "popover", "empty",
                 "color_picker", "select_slider", "data_editor"):
        setattr(mod, name, _Widget(rec, name))

    mod.sidebar = _Container(rec, "sidebar")
    mod.session_state = _SessionState(state or {})
    mod.column_config = SimpleNamespace(
        SelectboxColumn=lambda *a, **k: None,
        NumberColumn=lambda *a, **k: None,
        TextColumn=lambda *a, **k: None,
        DateColumn=lambda *a, **k: None)
    mod.cache_data = lambda *a, **k: (lambda f: f)
    mod.cache_resource = lambda *a, **k: (lambda f: f)
    mod.secrets = {}

    # ANYTHING ELSE THE REAL STREAMLIT HAS, AND NOTHING IT DOES NOT.
    #
    # The hand-written list above missed st.pydeck_chart, and the map
    # dashboard died on it. Listing every widget by hand is a list that will
    # be wrong again the next time a page uses something new.
    #
    # But a stub that answers to ANY name is worse than no stub: a typo -
    # st.markdwon - would be silently swallowed and the test would pass on a
    # page that renders nothing. So this falls back to the REAL module's
    # surface: a name Streamlit genuinely has becomes a recording widget, and
    # a name it does not raises exactly as it would in production.
    import streamlit as _real

    def _fallback(name):
        if name.startswith("_") or not hasattr(_real, name):
            raise AttributeError(
                f"streamlit has no attribute {name!r} - and neither does the "
                "stub, deliberately")
        w = _Widget(rec, name)
        setattr(mod, name, w)
        return w

    mod.__getattr__ = _fallback
    return mod, rec


@contextlib.contextmanager
def installed(state=None):
    """Swap the real streamlit for the stub, and put it back afterwards.

    Modules that did `import streamlit as st` at import time hold a reference
    to the old module, so those are re-pointed too - otherwise the page under
    test would keep drawing into the real Streamlit and the recorder would stay
    empty while the test passed.
    """
    mod, rec = make(state)
    saved = sys.modules.get("streamlit")
    sys.modules["streamlit"] = mod

    touched = []
    for name, m in list(sys.modules.items()):
        if m is None or not hasattr(m, "st"):
            continue
        if getattr(m, "__name__", "").startswith(("streamlit", "altair")):
            continue
        if getattr(m, "st", None) is saved:
            touched.append(m)
            m.st = mod
    try:
        yield rec
    finally:
        if saved is not None:
            sys.modules["streamlit"] = saved
        else:
            sys.modules.pop("streamlit", None)
        for m in touched:
            m.st = saved


def run(fn, *args, state=None, **kwargs):
    """Execute a page function under the stub and return what it rendered.

    st.stop() and st.rerun() end the page rather than failing the test: both
    are how a Streamlit page legitimately finishes early.
    """
    with installed(state) as rec:
        try:
            fn(*args, **kwargs)
        except _Stop:
            pass
    return rec
