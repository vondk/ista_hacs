"""Async client for istaonline.dk.

Ported from the synchronous ``istaonline_fetch`` scraper to aiohttp. istaonline
is an ASP.NET WebForms (Telerik) site with no JSON API: authentication is cookie
based and consumption data is obtained by driving the CSV export on PopUp.aspx.
"""
from __future__ import annotations

import csv as _csv
import html
import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import date, datetime

import aiohttp
from bs4 import BeautifulSoup

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import (
    BROWSER_HEADERS,
    DEFAULT_METER_TYPE,
    LOGIN_URL,
    POPUP_URL,
)
from .prices import heating_year_bounds

_LOGGER = logging.getLogger(__name__)


class IstaError(HomeAssistantError):
    """Generic ista Online error."""


class IstaAuthError(IstaError):
    """Raised when authentication fails (bad username/password)."""


class IstaConnectionError(IstaError):
    """Raised when istaonline.dk cannot be reached."""


@dataclass(frozen=True)
class Reading:
    """A single daily corrected-consumption reading for one meter."""

    meter_id: str
    room: str
    day: date
    value: float

    @property
    def key(self) -> str:
        """Unique key per meter/day, mirrors the (Måler, Dato) merge key."""
        return f"{self.meter_id}|{self.day.isoformat()}"


@dataclass(frozen=True)
class DiscoveredMeter:
    """A ``cons_id`` found on the logged-in dashboard page."""

    meter_type: str
    cons_id: str


@dataclass(frozen=True)
class ExportPeriod:
    """One selectable entry in the export's period dropdown."""

    value: str
    label: str


# The dashboard's "vis diagram"/"eksporter" buttons carry the real cons_id as an
# argument to one of these inline JS handlers, e.g.
# onclick="openTable(&#39;HCA&#39;,&#39;13958518442&#39;,&#39;&#39;);return false;"
# ASP.NET renders the quotes as HTML entities, so the raw response text must be
# unescaped before matching literal apostrophes.
_CONS_ID_RE = re.compile(
    r"open(?:Table|Chart)\('(?P<meter_type>[^']*)'\s*,\s*'(?P<cons_id>\d+)'"
)


def _extract_discovered_meters(page_html: str) -> list[DiscoveredMeter]:
    """Find distinct (meter_type, cons_id) pairs on the post-login page."""
    unescaped = html.unescape(page_html)
    seen: dict[str, DiscoveredMeter] = {}
    for match in _CONS_ID_RE.finditer(unescaped):
        cons_id = match.group("cons_id")
        if cons_id not in seen:
            seen[cons_id] = DiscoveredMeter(
                meter_type=match.group("meter_type") or DEFAULT_METER_TYPE,
                cons_id=cons_id,
            )
    return list(seen.values())


# The "from"/"to" period selectors on PopUp.aspx. The POST name uses "$", the
# rendered element id the same path with "_".
FROM_PERIOD_FIELD = "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxFromYear"
TO_PERIOD_FIELD = "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxToYear"


# How many heating years the dropdown offers. istaonline's selectors are
# Telerik load-on-demand combo boxes: the page ships "itemData":[] and fetches
# the list over AJAX, so it cannot be scraped from the HTML. The entries are
# formulaic though -- one per heating year, newest first -- so they are
# reproduced from the newest selectable year instead. Observed: eight entries,
# 01.05.2019 - 30.04.2020 through 01.05.2026 - 30.04.2027.
HISTORY_YEARS = 8

_PERIOD_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})")


def _client_state_field(field_name: str) -> str:
    """Name of the hidden state field Telerik pairs with a control."""
    return f"{field_name.replace('$', '_')}_ClientState"


def _combo_client_state(text: str) -> str:
    """Client state marking ``text`` as a RadComboBox's selected item.

    Posting the visible text input is not enough: the control restores its
    selection server-side from this hidden field and falls back to ViewState --
    the page's own default -- when it is empty. Measured against istaonline:
    text input alone returned the default range byte for byte, while adding
    this widened the same export from 486 to 2240 days.
    """
    return json.dumps(
        {
            "logEntries": [],
            "value": text,
            "text": text,
            "enabled": True,
            "checkedIndices": [],
            "checkedItemsTextOverflows": False,
        }
    )


def format_period(start_year: int) -> str:
    """Render a heating year the way the export's period selector expects it."""
    start, end = heating_year_bounds(start_year)
    return f"{start:%d.%m.%Y} - {end:%d.%m.%Y}"


def parse_period_year(value: str) -> int | None:
    """Return the heating year a ``01.05.YYYY - 30.04.YYYY`` value starts in."""
    match = _PERIOD_RE.search(value or "")
    return int(match.group(3)) if match else None


def _extract_period_options(page_html: str, field_name: str) -> list[ExportPeriod]:
    """Return the periods the export can start from, newest first.

    The newest selectable heating year is read off the page's own "to"
    selector, which defaults to it; the "from" selector sits one year lower.
    An empty list means the page could not be understood, and the caller
    should let ista pick the range as before.
    """
    fields = _hidden_fields(page_html, include_text=True)
    newest = parse_period_year(fields.get(TO_PERIOD_FIELD, ""))
    if newest is None:
        newest = parse_period_year(fields.get(field_name, ""))
    if newest is None:
        return []

    return [
        ExportPeriod(value=(period := format_period(year)), label=period)
        for year in range(newest, newest - HISTORY_YEARS, -1)
    ]


def _login_error_snippet(html: str) -> str:
    """Best-effort visible error text from a rejected login page, for debug logs."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = soup.find_all(
        id=re.compile("error|valid|msg", re.I)
    ) + soup.find_all(class_=re.compile("error|valid|msg", re.I))
    texts = [t for tag in candidates if (t := tag.get_text(strip=True))]
    if texts:
        return " | ".join(dict.fromkeys(texts))[:300]
    return soup.get_text(" ", strip=True)[:300]


def _hidden_fields(html: str, include_text: bool = False) -> dict[str, str]:
    """Collect the ASP.NET form fields a browser would post back.

    ``include_text`` is needed on PopUp.aspx: Telerik renders the period
    selectors as ``<input type="text">`` carrying the control's POST name, so
    hidden inputs alone miss the period entirely.
    """
    soup = BeautifulSoup(html, "html.parser")
    types = ["hidden", "text"] if include_text else ["hidden"]
    return {
        tag["name"]: tag.get("value", "")
        for tag in soup.find_all("input", type=types)
        if tag.get("name")
    }


def _parse_danish_float(raw: str) -> float | None:
    """Parse a Danish-formatted decimal (comma decimal separator)."""
    raw = (raw or "").strip().replace("\xa0", "").replace(" ", "")
    if not raw:
        return None
    # Danish CSV uses "," as decimal separator and rarely a "." thousands sep.
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_ddmmyyyy(raw: str) -> date | None:
    """Parse a ``DD-MM-YYYY`` date string."""
    raw = (raw or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _find_col(header: list[str], *candidates: str) -> int | None:
    """Return the index of the first header cell matching a candidate name."""
    normalised = [h.strip().lower() for h in header]
    for cand in candidates:
        cand = cand.lower()
        for idx, name in enumerate(normalised):
            if name == cand or cand in name:
                return idx
    return None


def parse_csv(raw_bytes: bytes) -> list[Reading]:
    """Parse the ista CSV export into a list of :class:`Reading`.

    Columns are located by header name (defensively) rather than by position:
    ``Måler`` (meter id), ``Dato`` (date), ``Korrigeret Forbrug`` (HCA units),
    ``Rum`` (room name).
    """
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    if len(lines) < 2:
        return []

    reader = _csv.reader(lines)
    header = next(reader)

    meter_idx = _find_col(header, "Måler", "Maaler", "Målernummer")
    date_idx = _find_col(header, "Dato", "Date")
    value_idx = _find_col(header, "Korrigeret Forbrug", "Forbrug", "Consumption")
    room_idx = _find_col(header, "Rum", "Room")

    if meter_idx is None or date_idx is None or value_idx is None:
        raise IstaError(
            f"Uventede CSV-kolonner fra ista: {header!r} "
            "(kan ikke finde Måler/Dato/Forbrug)"
        )

    readings: list[Reading] = []
    for row in reader:
        if not row or len(row) <= max(meter_idx, date_idx, value_idx):
            continue
        meter_id = row[meter_idx].strip()
        day = _parse_ddmmyyyy(row[date_idx])
        value = _parse_danish_float(row[value_idx])
        if not meter_id or day is None or value is None:
            continue
        room = row[room_idx].strip() if room_idx is not None and len(row) > room_idx else ""
        readings.append(Reading(meter_id=meter_id, room=room or meter_id, day=day, value=value))

    return readings


class IstaApiClient:
    """Fetches consumption data from istaonline.dk."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        cons_id: str = "",
        meter_type: str = DEFAULT_METER_TYPE,
        from_period: str = "",
        to_period: str = "",
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self._username = username
        self._password = password
        self._cons_id = cons_id
        self._meter_type = meter_type or DEFAULT_METER_TYPE
        self._from_period = from_period
        self._to_period = to_period

    def _session(self) -> aiohttp.ClientSession:
        """Create a session with its own cookie jar, owned by this client.

        ``auto_cleanup=False`` is required because we close the session
        ourselves; otherwise Home Assistant warns about a custom integration
        closing a session it manages.

        ``quote_cookie=False`` is required because istaonline's auth cookie
        (``.ASPXAUTH``) contains characters (``/``, ``+``) that Python's
        ``http.cookies`` wraps in quotes by default; ASP.NET treats those
        quotes as part of the cookie value, silently invalidating the
        session on every request after login.
        """
        return async_create_clientsession(
            self._hass,
            auto_cleanup=False,
            headers=BROWSER_HEADERS,
            cookie_jar=aiohttp.CookieJar(quote_cookie=False),
        )

    @staticmethod
    def _headers(**extra: str) -> dict[str, str]:
        """Per-request headers to send alongside the session's own.

        Home Assistant's shared client-session helper always forces its own
        ``User-Agent`` on requests, overriding whatever was passed to
        ``async_create_clientsession`` -- the only way to still look like a
        real browser (istaonline.dk started rejecting the Home Assistant UA
        after adding bot detection) is to repeat ``BROWSER_HEADERS`` on every
        individual request.
        """
        return {**BROWSER_HEADERS, **extra}

    async def async_fetch(
        self, from_period: str | None = None, to_period: str | None = None
    ) -> list[Reading]:
        """Log in and return the available readings for the configured period.

        A fresh aiohttp session (with its own cookie jar) is used per fetch so
        the stateful ASP.NET session is isolated to a single login/export cycle.
        """
        session = self._session()
        params = self._popup_params()
        try:
            await self._async_login(session)
            popup_url, page = await self._async_open_popup(session, params)
            raw = await self._async_export_csv(
                session,
                params,
                popup_url,
                page,
                self._from_period if from_period is None else from_period,
                self._to_period if to_period is None else to_period,
            )
            return parse_csv(raw)
        finally:
            session.detach()

    async def async_validate(self) -> bool:
        """Validate credentials by performing a full login + export."""
        await self.async_fetch()
        return True

    async def async_discover_meters(self) -> list[DiscoveredMeter]:
        """Log in and return the meters (cons_id/meter_type) found on the dashboard.

        Only needs username/password; ``cons_id`` is not required for this.
        """
        session = self._session()
        try:
            page = await self._async_login(session)
            return _extract_discovered_meters(page)
        finally:
            session.detach()

    async def async_discover_periods(self) -> list[ExportPeriod]:
        """Return the periods this login can export, newest first.

        An empty list means the dropdown could not be understood; callers
        should then let the page pick the period, as before.
        """
        session = self._session()
        try:
            await self._async_login(session)
            _, page = await self._async_open_popup(session, self._popup_params())
            return _extract_period_options(page, FROM_PERIOD_FIELD)
        finally:
            session.detach()

    async def _async_login(self, session: aiohttp.ClientSession) -> str:
        """Authenticate against Tenant.aspx and return the post-login page HTML."""
        try:
            async with session.get(LOGIN_URL, headers=self._headers()) as resp:
                resp.raise_for_status()
                login_page_url = str(resp.url)
                page = await resp.text()
        except aiohttp.ClientError as err:
            raise IstaConnectionError(f"Kan ikke nå istaonline.dk: {err}") from err

        fields = _hidden_fields(page)
        if "__VIEWSTATE" not in fields:
            raise IstaConnectionError("Ingen __VIEWSTATE på login-siden (uventet svar)")

        pw_state = json.dumps(
            {
                "enabled": True,
                "emptyMessage": "",
                "validationText": self._password,
                "valueAsString": self._password,
                "minDate": "1980-1-1",
                "maxDate": "2099-12-31",
                "lastSetTextBoxValue": self._password,
            }
        )

        data = {
            **fields,
            "ctl00$mainContent$hfFingerprint": "",
            "ctl00$mainContent$edtUserName": self._username,
            "ctl00$mainContent$edtPassword": self._password,
            "ctl00_mainContent_edtPassword_ClientState": pw_state,
            "__EVENTTARGET": "ctl00$mainContent$btnLogin",
            "__EVENTARGUMENT": "",
        }

        try:
            async with session.post(
                login_page_url,
                data=data,
                headers=self._headers(Referer=login_page_url),
            ) as resp:
                resp.raise_for_status()
                sent_ua = resp.request_info.headers.get("User-Agent")
                result = await resp.text()
        except aiohttp.ClientError as err:
            raise IstaConnectionError(f"Login-forespørgsel fejlede: {err}") from err

        if 'type="password"' in result.lower():
            _LOGGER.debug(
                "ista login rejected: status=%s sent_ua=%r final_url=%s body_snippet=%r",
                resp.status,
                sent_ua,
                resp.url,
                _login_error_snippet(result),
            )
            raise IstaAuthError("Login fejlede – tjek brugernavn/adgangskode")

        _LOGGER.debug("ista login OK for %s", self._username)
        return result

    def _popup_params(self) -> dict[str, str]:
        return {
            "Control": "PopUp_Table",
            "Metertype": self._meter_type,
            "cons_id": self._cons_id,
            "Culture": "",
            "rwndrnd": str(random.random()),
        }

    async def _async_open_popup(
        self, session: aiohttp.ClientSession, params: dict[str, str]
    ) -> tuple[str, str]:
        """GET PopUp.aspx and return ``(url, html)`` for a subsequent export."""
        try:
            async with session.get(
                POPUP_URL, params=params, headers=self._headers(Referer=LOGIN_URL)
            ) as resp:
                resp.raise_for_status()
                return str(resp.url), await resp.text()
        except aiohttp.ClientError as err:
            raise IstaConnectionError(f"Kan ikke hente PopUp.aspx: {err}") from err

    async def _async_export_csv(
        self,
        session: aiohttp.ClientSession,
        params: dict[str, str],
        popup_url: str,
        page: str,
        from_period: str = "",
        to_period: str = "",
    ) -> bytes:
        """Drive the CSV export on PopUp.aspx and return raw CSV bytes.

        An empty ``from_period``/``to_period`` keeps whatever the page has
        selected, which is ista's own default range.
        """
        fields = _hidden_fields(page, include_text=True)
        page_from = fields.get(FROM_PERIOD_FIELD, "")
        page_to = fields.get(TO_PERIOD_FIELD, "")
        from_period = from_period or page_from
        to_period = to_period or page_to
        _LOGGER.debug("ista export period: %r -> %r", from_period, to_period)

        # Changing a period only takes effect through the combo's client state;
        # leave it untouched when we are keeping the page's own selection.
        selection = {
            _client_state_field(field): _combo_client_state(chosen)
            for field, chosen, current in (
                (FROM_PERIOD_FIELD, from_period, page_from),
                (TO_PERIOD_FIELD, to_period, page_to),
            )
            if chosen != current
        }

        post_data = {
            **fields,
            **selection,
            FROM_PERIOD_FIELD: from_period,
            TO_PERIOD_FIELD: to_period,
            "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxType": "Målernummer",
            "ctl00$PopUpContentPlaceHolder$ctl00$CheckBox1": "on",  # kun data
            "ctl00$PopUpContentPlaceHolder$ctl00$CheckBox2": "on",  # alle sider
            "ctl00_PopUpContentPlaceHolder_ctl00_RadGrid1_ClientState": "",
            "__EVENTTARGET": "ctl00$PopUpContentPlaceHolder$ctl00$Button3",
            "__EVENTARGUMENT": "",
        }

        try:
            async with session.post(
                POPUP_URL, params=params, data=post_data,
                headers=self._headers(Referer=popup_url),
            ) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                body = await resp.read()
        except aiohttp.ClientError as err:
            raise IstaConnectionError(f"CSV-eksport fejlede: {err}") from err

        if "text/html" in content_type or body[:5] == b"<!DOC":
            # The server re-rendered the page instead of streaming CSV, which
            # usually means the session expired mid-flow.
            raise IstaError(
                "Serveren returnerede HTML i stedet for CSV (session udløbet?)"
            )

        return body
