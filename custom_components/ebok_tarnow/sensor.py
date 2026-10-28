from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfVolume
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EbokReadingSensor(coordinator, entry.entry_id),
        EbokDateSensor(coordinator, entry.entry_id),
        EbokConsumptionSensor(coordinator, entry.entry_id),
        EbokAccountBalanceSensor(coordinator, entry.entry_id),
        EbokLatestInvoiceAmountSensor(coordinator, entry.entry_id),
        EbokLatestInvoiceDueSensor(coordinator, entry.entry_id),
        EbokNearestUnpaidDueDateSensor(coordinator, entry.entry_id),
        EbokLatestInvoiceStatusSensor(coordinator, entry.entry_id),
        EbokLatestInvoicePaidSensor(coordinator, entry.entry_id),
    ])


class _EbokSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{self.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "eBOK Tarnów",
            "manufacturer": "Tarnowskie Wodociągi",
            "configuration_url": "https://ebok.tw.tarnow.pl/",
        }

    @property
    def native_value(self):
        return self.coordinator.data.get(self.value_key) if self.coordinator.data else None

    def _account_status_attributes(self):
        data = self.coordinator.data or {}
        return {
            "account_data_valid": data.get("account_data_valid"),
            "account_data_stale": data.get("account_data_stale"),
            "account_data_last_success": data.get("account_data_last_success"),
            "account_data_last_error": data.get("account_data_last_error"),
        }


class EbokReadingSensor(_EbokSensor):
    key = "reading"
    value_key = "reading_m3"
    _attr_name = "Stan licznika"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            "data_odczytu": data.get("reading_date"),
            "numer_licznika": data.get("meter_serial"),
            "adres": data.get("address"),
            "miejsce_montazu": data.get("installation"),
        }


class EbokDateSensor(_EbokSensor):
    key = "reading_date"
    value_key = "reading_date"
    _attr_name = "Data odczytu"
    _attr_icon = "mdi:calendar"


class EbokConsumptionSensor(_EbokSensor):
    key = "consumption"
    value_key = "consumption_m3"
    _attr_name = "Zużycie w ostatnim okresie"
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:water"


class _EbokMoneySensor(_EbokSensor):
    _attr_native_unit_of_measurement = "PLN"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    @property
    def extra_state_attributes(self):
        return self._account_status_attributes()


class EbokAccountBalanceSensor(_EbokMoneySensor):
    key = "account_balance"
    value_key = "account_balance"
    _attr_name = "Saldo konta"
    _attr_icon = "mdi:bank-outline"


class EbokLatestInvoiceAmountSensor(_EbokMoneySensor):
    key = "latest_invoice_amount"
    value_key = "invoice_gross"
    _attr_name = "Ostatnia faktura"
    _attr_icon = "mdi:receipt-text"

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            **self._account_status_attributes(),
            "numer_faktury": data.get("invoice_number"),
            "data_wystawienia": data.get("invoice_date"),
            "termin_platnosci": data.get("invoice_due_date"),
        }


class EbokLatestInvoiceDueSensor(_EbokMoneySensor):
    key = "latest_invoice_due"
    value_key = "invoice_due"
    _attr_name = "Do zapłaty za ostatnią fakturę"
    _attr_icon = "mdi:cash-clock"


class EbokNearestUnpaidDueDateSensor(_EbokSensor):
    key = "nearest_unpaid_due_date"
    value_key = "unpaid_invoice_due_date"
    _attr_name = "Termin najbliższej niezapłaconej faktury"
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-alert"

    @property
    def native_value(self):
        value = super().native_value
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {
            **self._account_status_attributes(),
            "неоплаченных_счетов": data.get("unpaid_invoice_count"),
            "неоплаченных_счетов_нет": data.get("no_unpaid_invoices"),
            "общий_остаток_по_неоплаченным_счетам": data.get("unpaid_invoice_balance"),
            "строк_счетов_пропущено": data.get("invoice_rows_skipped"),
            "строк_баланса_пропущено": data.get("balance_rows_skipped"),
        }


class EbokLatestInvoiceStatusSensor(_EbokSensor):
    key = "latest_invoice_status"
    value_key = "invoice_payment_status"
    _attr_name = "Status ostatniej faktury"
    _attr_icon = "mdi:receipt"

    @property
    def extra_state_attributes(self):
        return self._account_status_attributes()


class EbokLatestInvoicePaidSensor(_EbokMoneySensor):
    key = "latest_invoice_paid"
    value_key = "invoice_paid"
    _attr_name = "Opłacono za ostatnią fakturę"
    _attr_icon = "mdi:cash-check"
