"""Async client for istaonline.dk.

Ported from the synchronous ``istaonline_fetch`` scraper to aiohttp. istaonline
is an ASP.NET WebForms (Telerik) site with no JSON API: authentication is cookie
based and consumption data is obtained by driving the CSV export on PopUp.aspx.
"""
from __future__ import annotations

import csv as _csv
import json
import logging
import random
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


def _hidden_fields(html: str) -> dict[str, str]:
    """Collect all ASP.NET ``<input type="hidden">`` name/value pairs."""
    soup = BeautifulSoup(html, "html.parser")
    return {
        tag["name"]: tag.get("value", "")
        for tag in soup.find_all("input", type="hidden")
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
        cons_id: str,
        meter_type: str = DEFAULT_METER_TYPE,
    ) -> None:
        """Initialize the client."""
        self._hass = hass
        self._username = username
        self._password = password
        self._cons_id = cons_id
        self._meter_type = meter_type or DEFAULT_METER_TYPE

    async def async_fetch(self) -> list[Reading]:
        """Log in and return the latest available readings.

        A fresh aiohttp session (with its own cookie jar) is used per fetch so
        the stateful ASP.NET session is isolated to a single login/export cycle.
        """
        session = async_create_clientsession(
            self._hass, headers=BROWSER_HEADERS
        )
        try:
            await self._async_login(session)
            raw = await self._async_export_csv(session)
            return parse_csv(raw)
        finally:
            await session.close()

    async def async_validate(self) -> bool:
        """Validate credentials by performing a full login + export."""
        await self.async_fetch()
        return True

    async def _async_login(self, session: aiohttp.ClientSession) -> None:
        """Authenticate against Tenant.aspx."""
        try:
            async with session.get(LOGIN_URL) as resp:
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
                login_page_url, data=data, headers={"Referer": login_page_url}
            ) as resp:
                resp.raise_for_status()
                result = await resp.text()
        except aiohttp.ClientError as err:
            raise IstaConnectionError(f"Login-forespørgsel fejlede: {err}") from err

        if 'type="password"' in result.lower():
            raise IstaAuthError("Login fejlede – tjek brugernavn/adgangskode")

        _LOGGER.debug("ista login OK for %s", self._username)

    async def _async_export_csv(self, session: aiohttp.ClientSession) -> bytes:
        """Drive the CSV export on PopUp.aspx and return raw CSV bytes."""
        params = {
            "Control": "PopUp_Table",
            "Metertype": self._meter_type,
            "cons_id": self._cons_id,
            "Culture": "",
            "rwndrnd": str(random.random()),
        }

        try:
            async with session.get(
                POPUP_URL, params=params, headers={"Referer": LOGIN_URL}
            ) as resp:
                resp.raise_for_status()
                popup_url = str(resp.url)
                page = await resp.text()
        except aiohttp.ClientError as err:
            raise IstaConnectionError(f"Kan ikke hente PopUp.aspx: {err}") from err

        fields = _hidden_fields(page)
        from_period = fields.get(
            "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxFromYear", ""
        )
        to_period = fields.get(
            "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxToYear", ""
        )

        post_data = {
            **fields,
            "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxFromYear": from_period,
            "ctl00$PopUpContentPlaceHolder$ctl00$RadComboBoxToYear": to_period,
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
                headers={"Referer": popup_url},
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
