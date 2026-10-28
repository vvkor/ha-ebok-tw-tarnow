from __future__ import annotations

import asyncio
import logging
import re
import ssl
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import aiohttp
import certifi

_LOGGER = logging.getLogger(__name__)


class EbokError(Exception):
    """Portal request or response could not be processed."""


class EbokAuthError(EbokError):
    """Login was rejected or the session expired."""


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_target = False
        self.in_row = False
        self.in_cell = False
        self.depth = 0
        self.cell = ""
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "odczytyPodmiotuWykaz":
            self.in_target = True
            self.depth = 1
            return
        if not self.in_target:
            return
        if tag == "table":
            self.depth += 1
        elif tag == "tr":
            self.in_row, self.row = True, []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell, self.cell = True, ""

    def handle_data(self, data):
        if self.in_target and self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if not self.in_target:
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
                self.in_target = False


# Official Certum DV TLS G2 R39 intermediate certificate.
# SHA-256 fingerprint verified against the certificate downloaded from Certum:
# 83:C0:A5:A7:68:44:C8:40:DF:AF:82:0F:FD:02:AD:F6:57:3A:26:82:3E:F6:AF:75:8A:33:84:A0:AC:04:40:83
_CERTUM_DV_TLS_G2_R39_CA = """-----BEGIN CERTIFICATE-----
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
8FpPkganuCwy++7umTkM7+Q=
-----END CERTIFICATE-----"""


def _create_ssl_context() -> ssl.SSLContext:
    """Use normal system/certifi roots plus the verified missing Certum intermediate."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    context.load_verify_locations(cadata=_CERTUM_DV_TLS_G2_R39_CA)
    return context


def _parse_polish_number(value: str) -> float:
    normalized = value.replace("\xa0", "").replace(" ", "").strip()
    if not normalized:
        raise ValueError("empty numeric cell")
    return float(normalized.replace(",", "."))


def parse_readings_html(html: str) -> dict:
    parser = _TableParser()
    parser.feed(html)
    # Log only safe HTML structure; never log page text or cell values.
    table_tags = re.findall(r'<table\b[^>]*>', html, re.I)
    table_ids = [
        match.group(2)
        for tag in table_tags
        if (match := re.search(r'\bid\s*=\s*(["\'])(.*?)\1', tag, re.I))
    ]
    table_classes = [
        match.group(2)
        for tag in table_tags
        if (match := re.search(r'\bclass\s*=\s*(["\'])(.*?)\1', tag, re.I))
    ]
    _LOGGER.debug(
        "eBOK readings HTML structure: table_count=%s, table_ids=%s, "
        "table_classes=%s, html_length=%s, target_row_count=%s, "
        "target_row_cell_counts=%s, login_form_present=%s, password_field_present=%s",
        len(table_tags), table_ids[:20], table_classes[:20], len(html),
        len(parser.rows), [len(row) for row in parser.rows[:10]],
        bool(re.search(r'<form\b[^>]*action\s*=\s*["\'][^"\']*login', html, re.I)),
        bool(re.search(r'name\s*=\s*["\']password["\']', html, re.I)),
    )
    for row in (row for row in parser.rows if len(row) >= 7):
        try:
            reading = _parse_polish_number(row[4])
            consumption = _parse_polish_number(row[5])
        except (ValueError, IndexError):
            continue
        return {"meter_serial": row[0], "address": row[1], "installation": row[2], "reading_date": row[3], "reading_m3": reading, "consumption_m3": consumption, "unit": row[6]}
    raise EbokError("Не найден корректный ряд с показаниями в таблице odczytyPodmiotuWykaz; возможно, сессия не авторизована или HTML портала изменился.")


class EbokTarnowClient:
    def __init__(self, hass, base_url: str, username: str, password: str):
        self.hass = hass
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._pid: str | None = None

    async def _get_session(self):
        if self.session is None or self.session.closed:
            ssl_context = await self.hass.async_add_executor_job(_create_ssl_context)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30), headers={"User-Agent": "HomeAssistant-eBOK-Tarnow/0.1.3"})
        return self.session

    async def async_login(self):
        async with self._lock:
            session = await self._get_session()
            login_url = urljoin(self.base_url, "login")
            try:
                async with session.get(login_url) as response:
                    response.raise_for_status()
                    html = await response.text()
                    _LOGGER.debug("eBOK login page fetched: status=%s", response.status)
                csrf = re.search(r"<input(?=[^>]*name=[\"']_csrf[\"'])(?=[^>]*value=[\"']([^\"']+)[\"'])[^>]*>", html, re.I)
                if not csrf:
                    _LOGGER.warning("eBOK login page did not contain expected CSRF field")
                    raise EbokError("Не найдено поле CSRF на странице входа.")
                async with session.post(login_url, data={"_csrf": csrf.group(1), "username": self.username, "password": self.password}, allow_redirects=True) as response:
                    response.raise_for_status()
                    final_url = str(response.url)
                    body = await response.text()
                    _LOGGER.debug("eBOK login submit finished: status=%s path=%s", response.status, urlparse(final_url).path)
                if urlparse(final_url).path.rstrip("/") == "/login" or 'name="password"' in body.lower() or "name='password'" in body.lower():
                    _LOGGER.warning("eBOK login returned the login form again")
                    raise EbokAuthError("Вход отклонён порталом; проверьте учётные данные.")
                query_pid = parse_qs(urlparse(final_url).query).get("pid", [None])[0]
                if query_pid and query_pid.isdigit():
                    self._pid = query_pid
                pid_match = re.search(r"<select[^>]+id=[\"']pid[\"'][^>]*>[\s\S]*?<option[^>]+value=[\"'](\d+)[\"'][^>]*selected", body, re.I)
                if not pid_match:
                    pid_match = re.search(r"<input[^>]+name=[\"']pid[\"'][^>]+value=[\"'](\d+)[\"']", body, re.I)
                if pid_match:
                    self._pid = pid_match.group(1)
                if not self._pid:
                    landing_url = urljoin(self.base_url, "trust/podmiot")
                    async with session.get(landing_url, allow_redirects=True) as response:
                        response.raise_for_status()
                        final_landing_url = str(response.url)
                        landing_html = await response.text()
                        _LOGGER.debug("eBOK account landing page: status=%s path=%s", response.status, urlparse(final_landing_url).path)
                    landing_pid = parse_qs(urlparse(final_landing_url).query).get("pid", [None])[0]
                    if landing_pid and landing_pid.isdigit():
                        self._pid = landing_pid
                    if not self._pid:
                        landing_match = re.search(r"<select[^>]+id=[\"']pid[\"'][^>]*>[\s\S]*?<option[^>]+value=[\"'](\d+)[\"'][^>]*selected", landing_html, re.I)
                        if landing_match:
                            self._pid = landing_match.group(1)
                _LOGGER.debug("eBOK login completed; account id found=%s", bool(self._pid))
            except EbokError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.warning("eBOK TLS/network error during login: %s: %s", type(err).__name__, str(err))
                raise EbokError(f"Ошибка соединения при входе ({type(err).__name__}).") from err

    async def async_get_readings(self) -> dict:
        session = await self._get_session()
        url = urljoin(self.base_url, "trust/odczytyPodmiotu")
        # Match the portal's working browser request: the readings table
        # is rendered only when the date filter is submitted.
        params = []
        if self._pid:
            params.append(("pid", self._pid))
        params.extend([
            ("fid", "STD"),
            ("disp", "process"),
            ("filtrDataOd", f"{date.today().year}-01-01"),
            ("filtrTyp", "WG_DATY"),
            # Browser submits both the hidden field and the submit button.
            ("disp", "process"),
        ])
        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                final_url = str(response.url)
                html = await response.text()
                _LOGGER.debug("eBOK readings page fetched: status=%s path=%s", response.status, urlparse(final_url).path)
            if urlparse(final_url).path.rstrip("/") == "/login" or 'name="password"' in html.lower() or "name='password'" in html.lower():
                _LOGGER.debug("eBOK readings request redirected to login; retrying authentication")
                await self.async_login()
                # Match the portal's working browser request: the readings table
                # is rendered only when the date filter is submitted.
                params = []
                if self._pid:
                    params.append(("pid", self._pid))
                params.extend([
                    ("fid", "STD"),
                    ("disp", "process"),
                    ("filtrDataOd", f"{date.today().year}-01-01"),
                    ("filtrTyp", "WG_DATY"),
                    # Browser submits both the hidden field and the submit button.
                    ("disp", "process"),
                ])
                async with session.get(url, params=params) as response:
                    response.raise_for_status()
                    final_url = str(response.url)
                    html = await response.text()
                    _LOGGER.debug("eBOK readings retry finished: status=%s path=%s", response.status, urlparse(final_url).path)
            result = parse_readings_html(html)
            _LOGGER.debug("eBOK readings parsed successfully; date=%s", result.get("reading_date"))
            return result
        except EbokError as err:
            _LOGGER.warning("eBOK readings validation failed: %s", err)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("eBOK network error while fetching readings: %s", type(err).__name__)
            raise EbokError(f"Ошибка загрузки показаний ({type(err).__name__}).") from err

    async def async_get_account_data(self) -> dict:
        """Fetch invoice/payment overview and the account balance summary."""
        session = await self._get_session()

        async def fetch(path: str) -> str:
            params = []
            if self._pid:
                params.append(("pid", self._pid))
            params.append(("fid", "STD"))
            async with session.get(urljoin(self.base_url, path), params=params, allow_redirects=True) as response:
                response.raise_for_status()
                final_url = str(response.url)
                html = await response.text()
            if urlparse(final_url).path.rstrip("/") == "/login" or 'name="password"' in html.lower() or "name='password'" in html.lower():
                raise EbokAuthError("eBOK session expired")
            return html

        try:
            try:
                invoices_html = await fetch("trust/faktury")
                balances_html = await fetch("trust/saldawpl")
            except EbokAuthError:
                await self.async_login()
                invoices_html = await fetch("trust/faktury")
                balances_html = await fetch("trust/saldawpl")
            data = parse_account_pages(invoices_html, balances_html)
            _LOGGER.debug(
                "eBOK account overview parsed: invoice_found=%s balance_found=%s unpaid_count=%s",
                bool(data.get("invoice_number")),
                data.get("account_balance") is not None,
                data.get("unpaid_invoice_count"),
            )
            return data
        except EbokError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("eBOK network error while fetching account data: %s", type(err).__name__)
            raise EbokError(f"Ошибка загрузки счетов и баланса ({type(err).__name__}).") from err

    async def async_close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class _AccountPageParser(HTMLParser):
    """Extract invoice/balance rows and the summary balance panel."""

    def __init__(self, table_id: str):
        super().__init__(convert_charrefs=True)
        self.table_id = table_id
        self.table_found = False
        self.in_target = False
        self.depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell = ""
        self.row = []
        self.rows = []
        self.in_summary = False
        self.summary_heading = ""
        self.in_heading = False
        self.in_summary_text = False
        self.summary_text = ""
        self.summary = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == self.table_id:
            self.table_found = True
            self.in_target = True
            self.depth = 1
            return
        if tag == "section" and "sub-nav" in attrs.get("class", ""):
            self.in_summary = True
        if self.in_target:
            if tag == "table":
                self.depth += 1
            elif tag == "tr":
                self.in_row, self.row = True, []
            elif tag in ("td", "th") and self.in_row:
                self.in_cell, self.cell = True, ""
        if self.in_summary:
            classes = attrs.get("class", "")
            if tag == "h5" and "list-group-item-heading" in classes:
                self.in_heading = True
                self.summary_heading = ""
            elif tag == "p" and "list-group-item-text" in classes:
                self.in_summary_text = True
                self.summary_text = ""

    def handle_data(self, data):
        if self.in_target and self.in_cell:
            self.cell += data
        if self.in_summary and self.in_heading:
            self.summary_heading += data
        if self.in_summary and self.in_summary_text:
            self.summary_text += data

    def handle_endtag(self, tag):
        if self.in_target:
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
                    self.in_target = False
        if self.in_summary and tag == "h5" and self.in_heading:
            self.in_heading = False
        elif self.in_summary and tag == "p" and self.in_summary_text:
            heading = " ".join(self.summary_heading.split()).rstrip(":")
            value = " ".join(self.summary_text.split())
            if heading:
                self.summary[heading] = value
            self.in_summary_text = False
        elif tag == "section" and self.in_summary:
            self.in_summary = False


def _money(value: str) -> float:
    normalized = value.replace("\xa0", "").replace(" ", "").replace("zł", "").strip()
    if not normalized:
        raise ValueError("empty monetary value")
    return float(normalized.replace(",", "."))


def _parse_account_page(html: str, table_id: str) -> tuple[list[list[str]], dict[str, str], bool]:
    parser = _AccountPageParser(table_id)
    parser.feed(html)
    return parser.rows, parser.summary, parser.table_found


def parse_account_pages(invoices_html: str, balances_html: str) -> dict:
    """Parse invoice and balance pages; reject missing/changed tables instead of inventing zeros."""
    invoice_rows, _, invoices_table_found = _parse_account_page(invoices_html, "fakturyWykaz")
    balance_rows, summary, balances_table_found = _parse_account_page(balances_html, "saldaWplatyWykaz")

    if not invoices_table_found:
        raise EbokError("На странице eBOK не найдена таблица фактур fakturyWykaz.")
    if not balances_table_found:
        raise EbokError("На странице eBOK не найдена таблица баланса saldaWplatyWykaz.")

    invoices = []
    malformed_invoice_rows = 0
    for row in invoice_rows:
        if not row or row[0].strip().lower().startswith(("nr faktury", "numer faktury")):
            continue
        if len(row) < 6:
            malformed_invoice_rows += 1
            continue
        try:
            issued = date.fromisoformat(row[2].strip())
            due_date = date.fromisoformat(row[3].strip())
            gross = _parse_polish_number(row[4])
            paid = _parse_polish_number(row[5])
        except (ValueError, IndexError):
            malformed_invoice_rows += 1
            continue
        if gross < 0 or paid < 0:
            malformed_invoice_rows += 1
            continue
        due = max(0.0, gross - paid)
        invoices.append({
            "invoice_number": row[0].strip(),
            "invoice_date": issued.isoformat(),
            "invoice_due_date": due_date.isoformat(),
            "invoice_gross": round(gross, 2),
            "invoice_paid": round(paid, 2),
            "invoice_due": round(due, 2),
            "invoice_payment_status": "Оплачена" if due <= 0.01 else ("Частично оплачена" if paid > 0 else "Не оплачена"),
        })

    if not invoices:
        raise EbokError(
            f"Таблица фактур найдена, но ни одна строка не распознана (строк таблицы: {len(invoice_rows)})."
        )

    # A balance table with no rows is ambiguous: it may mean no debt, or a changed/empty page.
    # Require at least one row (often the header or an explicit empty-state row) before reporting zero debt.
    if not balance_rows:
        raise EbokError("Таблица баланса найдена, но не содержит строк; сумму долга не считаем нулевой.")

    invoices.sort(key=lambda item: item["invoice_date"], reverse=True)
    unpaid = []
    malformed_balance_rows = 0
    for row in balance_rows:
        if not row or row[0].strip().lower().startswith(("nr faktury", "numer faktury")):
            continue
        if len(row) < 6:
            # Header or explicit no-results row isn't an invoice record.
            continue
        try:
            due_date = date.fromisoformat(row[3].strip())
            gross = _parse_polish_number(row[4])
            balance = _parse_polish_number(row[5])
        except (ValueError, IndexError):
            malformed_balance_rows += 1
            continue
        if abs(balance) > 0.009:
            unpaid.append({
                "due_date": due_date.isoformat(),
                "gross": round(gross, 2),
                "balance": round(balance, 2),
                "number": row[0].strip(),
            })
    unpaid.sort(key=lambda item: item["due_date"])

    result = dict(invoices[0])
    summary_found = {}
    for source, target in (
        ("Saldo", "account_balance"),
        ("Należności bieżące", "current_receivables"),
        ("Należności zaległe", "overdue_receivables"),
    ):
        value = summary.get(source)
        if value is not None:
            try:
                summary_found[target] = round(_money(value), 2)
            except ValueError:
                _LOGGER.warning("eBOK account summary contains an invalid amount for %s", source)
    result.update(summary_found)

    result["unpaid_invoice_due_date"] = unpaid[0]["due_date"] if unpaid else None
    result["unpaid_invoice_balance"] = round(sum(item["balance"] for item in unpaid), 2)
    result["unpaid_invoice_count"] = len(unpaid)
    result["account_data_valid"] = True
    result["invoice_rows_parsed"] = len(invoices)
    result["invoice_rows_skipped"] = malformed_invoice_rows
    result["balance_rows_skipped"] = malformed_balance_rows
    result["no_unpaid_invoices"] = len(unpaid) == 0
    _LOGGER.debug(
        "eBOK account data parsed: invoices=%s invoice_rows_skipped=%s balance_rows=%s "
        "balance_rows_skipped=%s unpaid=%s summary_fields=%s",
        len(invoices), malformed_invoice_rows, len(balance_rows),
        malformed_balance_rows, len(unpaid), sorted(summary_found),
    )
    return result
