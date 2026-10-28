from __future__ import annotations

import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import EbokError, EbokTarnowClient
from .const import CONF_BASE_URL, CONF_PASSWORD, CONF_USERNAME, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

_ACCOUNT_KEYS = (
    "invoice_number",
    "invoice_date",
    "invoice_due_date",
    "invoice_gross",
    "invoice_paid",
    "invoice_due",
    "invoice_payment_status",
    "account_balance",
    "current_receivables",
    "overdue_receivables",
    "unpaid_invoice_due_date",
    "unpaid_invoice_balance",
    "unpaid_invoice_count",
    "invoice_rows_parsed",
    "invoice_rows_skipped",
    "balance_rows_skipped",
    "no_unpaid_invoices",
)


class EbokTarnowCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.client = EbokTarnowClient(
            hass,
            entry.data[CONF_BASE_URL],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self):
        try:
            try:
                readings = await self.client.async_get_readings()
            except EbokError:
                await self.client.async_login()
                readings = await self.client.async_get_readings()

            previous = self.data or {}
            try:
                account = await self.client.async_get_account_data()
                readings.update(account)
                readings["account_data_valid"] = True
                readings["account_data_last_success"] = dt_util.utcnow().isoformat()
                readings["account_data_stale"] = False
            except EbokError as account_err:
                _LOGGER.warning(
                    "eBOK account data refresh failed; retaining last-known values as stale: %s",
                    account_err,
                )
                readings.update(
                    {key: previous[key] for key in _ACCOUNT_KEYS if key in previous}
                )
                # Never let a retained last-known value masquerade as fresh.
                readings["account_data_valid"] = False
                readings["account_data_stale"] = True
                readings["account_data_last_success"] = previous.get(
                    "account_data_last_success"
                )
                readings["account_data_last_error"] = str(account_err)[:300]

            return readings
        except EbokError as err:
            raise UpdateFailed(str(err)) from err
