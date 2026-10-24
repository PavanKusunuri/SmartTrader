"""Static guard: no code path in app/ can reach an order, position or funds endpoint.

CLAUDE.md §3: no live order placement, no code path that could accidentally
submit one. These checks read the source text, so they fail the moment someone
adds an order call anywhere, not only inside the Groww client.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
SOURCES = [p for p in APP.rglob("*.py")]
STATIC = [p for p in (APP / "api" / "static").glob("*.js") if p.name != "tailwind.js"]

# A string literal that starts like an API path/URL for orders, smart orders,
# portfolio/positions/holdings or margin/funds.
ORDER_PATH = re.compile(
    r"""["'`}](?:https?://[^"'`\s]*?)?/(?:v\d/)?(?:order|orders|order-advance|smart-order|smart-orders|"""
    r"""positions?|holdings|margins?|portfolio)\b""",
    re.IGNORECASE,
)
ORDER_CALLS = re.compile(
    r"\b(?:place_order|modify_order|cancel_order|create_smart_order|modify_smart_order|"
    r"cancel_smart_order|GrowwAPI)\b"
)
SDK_IMPORT = re.compile(r"^\s*(?:from|import)\s+growwapi\b", re.MULTILINE)


def test_sources_were_found():
    assert len(SOURCES) > 30


def test_no_order_endpoint_literals_in_python_or_js():
    offenders = [
        f"{p.relative_to(APP.parent)}: {m.group(0)}"
        for p in SOURCES + STATIC
        for m in ORDER_PATH.finditer(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_order_method_calls_or_sdk_class():
    offenders = [
        f"{p.relative_to(APP.parent)}: {m.group(0)}"
        for p in SOURCES
        for m in ORDER_CALLS.finditer(p.read_text(encoding="utf-8"))
    ]
    # Docstrings may *name* the SDK class to explain why it is not used.
    offenders = [o for o in offenders if "groww_client.py" not in o and "groww_provider.py" not in o]
    assert offenders == []


def test_official_sdk_is_never_imported():
    assert [p.name for p in SOURCES if SDK_IMPORT.search(p.read_text(encoding="utf-8"))] == []


def test_only_known_modules_make_outbound_http_calls():
    importers = {
        p.relative_to(APP).as_posix()
        for p in SOURCES
        if re.search(r"^\s*(?:import|from)\s+(?:httpx|requests|urllib\.request|aiohttp|socket)\b",
                     p.read_text(encoding="utf-8"), re.MULTILINE)
    }
    assert importers == {"data/groww_client.py", "alerts/telegram.py"}


def test_only_known_hosts_are_referenced():
    hosts = set()
    for p in SOURCES:
        hosts |= set(re.findall(r"https?://([A-Za-z0-9.-]+)", p.read_text(encoding="utf-8")))
    allowed = {"api.groww.in", "api.telegram.org", "growwapi-assets.groww.in", "groww.in", "www.nseindia.com",
               "nseindia.com", "127.0.0.1", "localhost", "github.com", "example.com"}
    assert hosts - allowed == set(), hosts - allowed
