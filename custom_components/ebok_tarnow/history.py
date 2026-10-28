"""Import eBOK meter readings into Home Assistant long-term statistics."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import voluptuous as vol
from homeassistant.components.recorder.statistics import (
    StatisticMeanType,
    statistics_during_period,
    async_import_statistics,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
CONF_IMPORT_HISTORY = "import_history"
DEFAULT_IMPORT_HISTORY = True


class _ReadingsTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.target = False
        self.depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell = ""
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "odczytyPodmiotuWykaz":
            self.target, self.depth = True, 1
            return
        if not self.target:
            return
        if tag == "table":
            self.depth += 1
        elif tag == "tr":
            self.in_row, self.row = True, []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell, self.cell = True, ""

    def handle_data(self, data):
        if self.target and self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if not self.target:
            return
        if tag in ("td", "th") and self.in_cell:
            self.row.append(" ".join(self.cell.split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.in_row = False
        elif tag == "table":
            self.depth -= 1
            if self.depth <= 0:
                self.target = False


def _number(value: str) -> float:
    return float(value.replace("\xa0", "").replace(" ", "").replace(",", ".").strip())


def parse_readings_history(html: str, start_date: date, end_date: date) -> list[dict]:
    parser = _ReadingsTable()
    parser.feed(html)
    readings = []
    for row in parser.rows:
        if len(row) < 7:
            continue
        try:
            # Portal format observed in HAR: dd.mm.yyyy
            raw_date = row[3].strip()
            try:
                reading_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
            except ValueError:
                reading_date = date.fromisoformat(raw_date[:10])
            reading = _number(row[4])
            consumption = _number(row[5])
        except (ValueError, IndexError):
            continue
        if start_date <= reading_date <= end_date:
            readings.append({
                "reading_date": reading_date,
                "reading_m3": reading,
                "consumption_m3": consumption,
                "meter_serial": row[0],
                "address": row[1],
                "installation": row[2],
            })
    # Keep the last occurrence for a given date, and sort chronologically.
    by_date = {item["reading_date"]: item for item in readings}
    return [by_date[day] for day in sorted(by_date)]


async def async_import_reading_history(hass, entry, client) -> int:
    """Fetch and import dated cumulative readings; never changes entity state."""
    if not entry.options.get(CONF_IMPORT_HISTORY, DEFAULT_IMPORT_HISTORY):
        _LOGGER.debug("eBOK historical import disabled in options")
        return 0

    end_date = dt_util.now().date()
    start_date = end_date - timedelta(days=365)
    if client.session is None or client.session.closed:
        await client._get_session()
    url = urljoin(client.base_url, "trust/odczytyPodmiotu")
    params = []
    if client._pid:
        params.append(("pid", client._pid))
    params.extend([
        ("fid", "STD"),
        ("disp", "process"),
        ("filtrDataOd", start_date.isoformat()),
        ("filtrTyp", "WG_DATY"),
        ("disp", "process"),
    ])
    async with client.session.get(url, params=params) as response:
        response.raise_for_status()
        final_url = str(response.url)
        html = await response.text()
    if final_url.rstrip("/").endswith("/login") or re.search(r'name=["\']password["\']', html, re.I):
        await client.async_login()
        params = []
        if client._pid:
            params.append(("pid", client._pid))
        params.extend([
            ("fid", "STD"), ("disp", "process"),
            ("filtrDataOd", start_date.isoformat()), ("filtrTyp", "WG_DATY"),
            ("disp", "process"),
        ])
        async with client.session.get(url, params=params) as response:
            response.raise_for_status()
            html = await response.text()

    readings = parse_readings_history(html, start_date, end_date)
    if not readings:
        raise ValueError("eBOK returned no historical readings for the requested date range")

    # Use the existing sensor's statistic ID so these points appear in its history.
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_reading"
    entity_id = registry.async_get_entity_id("sensor", "ebok_tarnow", unique_id)
    if not entity_id:
        raise ValueError("Could not resolve the registered eBOK meter sensor entity_id")

    # Avoid importing points newer than the currently reported meter state.
    current = hass.states.get(entity_id)
    try:
        current_value = float(current.state) if current else None
    except (TypeError, ValueError):
        current_value = None

    # Query the Recorder before importing: async_import_statistics replaces
    # rows with matching timestamps. Importing every portal reading on each
    # integration setup could therefore overwrite statistics already in HA.
    query_start = dt_util.as_utc(
        datetime.combine(
            start_date,
            time.min,
            tzinfo=dt_util.get_time_zone(hass.config.time_zone),
        )
    )
    existing = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        query_start,
        dt_util.utcnow(),
        {entity_id},
        "day",
        None,
        {"state", "sum"},
    )
    existing_dates = {
        dt_util.as_local(
            datetime.fromtimestamp(row["start"] / 1000, tz=dt_util.UTC)
        ).date()
        for row in existing.get(entity_id, [])
        if row.get("start") is not None
    }

    stats = []
    seen = set()
    skipped_existing = 0
    for item in readings:
        reading_date = item["reading_date"]
        value = float(item["reading_m3"])
        if current_value is not None and value > current_value:
            _LOGGER.warning(
                "Skipping eBOK historical point above current meter value: date=%s",
                reading_date,
            )
            continue
        if reading_date in existing_dates:
            skipped_existing += 1
            continue
        local_start = datetime.combine(
            reading_date,
            time.min,
            tzinfo=dt_util.get_time_zone(hass.config.time_zone),
        )
        utc_start = dt_util.as_utc(local_start)
        # Recorder statistics are hourly: align to the beginning of the local date.
        key = utc_start.isoformat()
        if key in seen:
            continue
        seen.add(key)
        stats.append({"start": utc_start, "state": value, "sum": value})

    if not stats:
        _LOGGER.info(
            "eBOK history import skipped: no missing statistic dates (existing dates skipped=%s)",
            skipped_existing,
        )
        return 0

    metadata = {
        "statistic_id": entity_id,
        "source": "recorder",
        "name": None,
        "unit_of_measurement": "m³",
        "unit_class": "volume",
        "has_sum": True,
        "mean_type": StatisticMeanType.NONE,
    }
    async_import_statistics(hass, metadata, stats)
    _LOGGER.info(
        "Queued import of %s missing historical eBOK meter readings (%s to %s; existing dates skipped=%s)",
        len(stats), start_date, end_date, skipped_existing,
    )
    return len(stats)
