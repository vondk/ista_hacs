"""Tests for the CSV parsing in the ista API client."""
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ista_online.api import (
    FROM_PERIOD_FIELD,
    TO_PERIOD_FIELD,
    DiscoveredMeter,
    IstaApiClient,
    Reading,
    _extract_discovered_meters,
    _extract_period_options,
    _parse_danish_float,
    _parse_ddmmyyyy,
    format_period,
    parse_csv,
    parse_period_year,
)

# Trimmed excerpt of the buttons istaonline.dk renders on the logged-in
# dashboard; the real cons_id only appears as an argument to these inline
# onclick handlers. ASP.NET encodes the quotes as &#39; entities, which is
# what actually comes back over the wire (not the literal apostrophes a
# browser's live DOM/Elements view shows) -- the regex must unescape first.
DASHBOARD_HTML = """
<input type="image" onclick="openConsumptionDiff(&#39;13958518442&#39;,&#39;01-05-2021&#39;);return false;" />
<input type="image" onclick="openChart(&#39;HCA&#39;,&#39;13958518442&#39;,&#39;&#39;);return false;" />
<input type="image" onclick="openTable(&#39;HCA&#39;,&#39;13958518442&#39;,&#39;&#39;);return false;" />
"""


SAMPLE_CSV = (
    '"Måler","Dato","Korrigeret Forbrug","Rum"\r\n'
    '"735493320","14-05-2019","12,5","Børneværelse"\r\n'
    '"735493283","14-05-2019","0","Soveværelse"\r\n'
    '"735493306","15-05-2019","3,75","Bryggers"\r\n'
    '"","15-05-2019","9,9","Ukendt"\r\n'  # missing meter id -> skipped
).encode("utf-8-sig")


def test_parse_danish_float():
    assert _parse_danish_float("1234,5") == 1234.5
    assert _parse_danish_float("1.234,56") == 1234.56
    assert _parse_danish_float("") is None
    assert _parse_danish_float("abc") is None


def test_parse_ddmmyyyy():
    assert _parse_ddmmyyyy("14-05-2019") == date(2019, 5, 14)
    assert _parse_ddmmyyyy("bad") is None


def test_parse_csv():
    readings = parse_csv(SAMPLE_CSV)
    assert len(readings) == 3  # row with empty meter id dropped
    by_id = {r.meter_id: r for r in readings}
    assert by_id["735493320"].value == 12.5
    assert by_id["735493320"].room == "Børneværelse"
    assert by_id["735493320"].day == date(2019, 5, 14)


def test_reading_key_unique():
    r = Reading("111", "Stue", date(2025, 5, 1), 3.0)
    assert r.key == "111|2025-05-01"


def test_parse_csv_empty():
    assert parse_csv(b"") == []


def test_extract_discovered_meters():
    meters = _extract_discovered_meters(DASHBOARD_HTML)
    assert meters == [DiscoveredMeter(meter_type="HCA", cons_id="13958518442")]


def test_extract_discovered_meters_dedupes_multiple_rooms():
    html = DASHBOARD_HTML * 2 + (
        '<input onclick="openTable(&#39;HCA&#39;,&#39;99999&#39;,&#39;&#39;);return false;" />'
    )
    meters = _extract_discovered_meters(html)
    assert [m.cons_id for m in meters] == ["13958518442", "99999"]


def test_extract_discovered_meters_none_found():
    assert _extract_discovered_meters("<html></html>") == []


# --- period dropdown -------------------------------------------------------

FROM_ID = FROM_PERIOD_FIELD.replace("$", "_")
TO_ID = TO_PERIOD_FIELD.replace("$", "_")

# Trimmed from a real PopUp.aspx capture: the period selectors are TEXT inputs
# carrying the control's POST name (the id gets an "_Input" suffix), and their
# values are heating-year date ranges. Hidden inputs alone miss them entirely.
REAL_POPUP_HTML = f"""
<html><body>
<input type="hidden" name="__VIEWSTATE" value="/wEPDwUK..." />
<input type="hidden" name="__EVENTVALIDATION" value="/wEdAAY..." />
<div id="{FROM_ID}" class="RadComboBox RadComboBox_WebBlue">
  <input name="{FROM_PERIOD_FIELD}" type="text" class="rcbInput"
         id="{FROM_ID}_Input" value="01.05.2025 - 30.04.2026" />
  <input id="{FROM_ID}_ClientState" name="{FROM_ID}_ClientState" type="hidden" />
</div>
<div id="{TO_ID}" class="RadComboBox RadComboBox_WebBlue">
  <input name="{TO_PERIOD_FIELD}" type="text" class="rcbInput"
         id="{TO_ID}_Input" value="01.05.2026 - 30.04.2027" />
  <input id="{TO_ID}_ClientState" name="{TO_ID}_ClientState" type="hidden" />
</div>
</body></html>
"""

def test_format_and_parse_period_round_trip():
    assert format_period(2025) == "01.05.2025 - 30.04.2026"
    assert parse_period_year("01.05.2025 - 30.04.2026") == 2025
    assert parse_period_year("") is None
    assert parse_period_year("hulubulu") is None


def test_extract_period_options_reproduces_the_dropdown():
    """The list ista shows: one heating year per entry, newest first.

    The selectors are load-on-demand, so the entries are generated from the
    newest selectable year rather than scraped. Verified against the live
    dropdown, which offers 01.05.2019-30.04.2020 through 01.05.2026-30.04.2027.
    """
    periods = _extract_period_options(REAL_POPUP_HTML, FROM_PERIOD_FIELD)

    assert [p.value for p in periods] == [
        "01.05.2026 - 30.04.2027",
        "01.05.2025 - 30.04.2026",
        "01.05.2024 - 30.04.2025",
        "01.05.2023 - 30.04.2024",
        "01.05.2022 - 30.04.2023",
        "01.05.2021 - 30.04.2022",
        "01.05.2020 - 30.04.2021",
        "01.05.2019 - 30.04.2020",
    ]
    assert all(p.label == p.value for p in periods)


def test_extract_period_options_follows_the_page_forward():
    """A year on, the same page yields a list shifted one year later."""
    html = REAL_POPUP_HTML.replace("01.05.2026 - 30.04.2027", "01.05.2027 - 30.04.2028")
    periods = _extract_period_options(html, FROM_PERIOD_FIELD)

    assert periods[0].value == "01.05.2027 - 30.04.2028"
    assert periods[-1].value == "01.05.2020 - 30.04.2021"


def test_extract_period_options_unknown_markup():
    assert _extract_period_options("<html></html>", FROM_PERIOD_FIELD) == []


# --- export period plumbing ------------------------------------------------


def _mock_session(captured: dict):
    """A session whose POST records the form data and returns CSV bytes."""

    class _Ctx:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *exc):
            return False

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.headers = {"Content-Type": "text/csv"}
    response.read = AsyncMock(return_value=SAMPLE_CSV)

    session = MagicMock()

    def _post(url, params=None, data=None, headers=None):
        captured["data"] = data
        return _Ctx(response)

    session.post = _post
    return session


@pytest.mark.asyncio
async def test_export_uses_the_chosen_period():
    captured: dict = {}
    client = IstaApiClient(MagicMock(), "user", "pw", cons_id="1")
    body = await client._async_export_csv(
        _mock_session(captured),
        {"Control": "PopUp_Table"},
        "https://www.istaonline.dk/PopUp.aspx",
        REAL_POPUP_HTML,
        from_period="01.05.2020 - 30.04.2021",
        to_period="01.05.2026 - 30.04.2027",
    )

    assert body == SAMPLE_CSV
    assert captured["data"][FROM_PERIOD_FIELD] == "01.05.2020 - 30.04.2021"
    assert captured["data"][TO_PERIOD_FIELD] == "01.05.2026 - 30.04.2027"
    # The ViewState must still ride along or the server rejects the postback.
    assert captured["data"]["__VIEWSTATE"] == "/wEPDwUK..."

    # Without the client state the server silently keeps its own default, so
    # the changed "from" must carry one -- and the unchanged "to" must not.
    state = json.loads(captured["data"][f"{FROM_ID}_ClientState"])
    assert state["value"] == "01.05.2020 - 30.04.2021"
    assert state["text"] == "01.05.2020 - 30.04.2021"
    # "to" is unchanged, so its state stays as the page rendered it: empty.
    assert captured["data"][f"{TO_ID}_ClientState"] == ""


@pytest.mark.asyncio
async def test_export_falls_back_to_the_page_default():
    """The period lives in a text input, so it must still be posted back."""
    captured: dict = {}
    client = IstaApiClient(MagicMock(), "user", "pw", cons_id="1")
    await client._async_export_csv(
        _mock_session(captured),
        {"Control": "PopUp_Table"},
        "https://www.istaonline.dk/PopUp.aspx",
        REAL_POPUP_HTML,
    )

    assert captured["data"][FROM_PERIOD_FIELD] == "01.05.2025 - 30.04.2026"
    assert captured["data"][TO_PERIOD_FIELD] == "01.05.2026 - 30.04.2027"
    # Nothing changed, so both states stay empty and the page keeps its own.
    assert captured["data"][f"{FROM_ID}_ClientState"] == ""
    assert captured["data"][f"{TO_ID}_ClientState"] == ""
