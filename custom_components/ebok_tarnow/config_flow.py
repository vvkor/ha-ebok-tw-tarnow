from __future__ import annotations

import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from .client import EbokAuthError, EbokError, EbokTarnowClient
from .const import CONF_BASE_URL, CONF_PASSWORD, CONF_USERNAME, DEFAULT_BASE_URL, DOMAIN
from .history import CONF_IMPORT_HISTORY, DEFAULT_IMPORT_HISTORY

_LOGGER = logging.getLogger(__name__)

class CannotConnect(HomeAssistantError):
    """Unable to connect to eBOK or parse readings."""

class InvalidAuth(HomeAssistantError):
    """eBOK rejected the provided credentials."""

async def _validate(hass: HomeAssistant, data: dict) -> None:
    client = EbokTarnowClient(hass, data[CONF_BASE_URL], data[CONF_USERNAME], data[CONF_PASSWORD])
    try:
        await client.async_login()
        await client.async_get_readings()
    finally:
        await client.async_close()

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            user_input[CONF_BASE_URL] = user_input[CONF_BASE_URL].rstrip("/")
            await self.async_set_unique_id(user_input[CONF_BASE_URL].lower())
            self._abort_if_unique_id_configured()
            try:
                await _validate(self.hass, user_input)
            except EbokAuthError:
                _LOGGER.warning("eBOK config flow: portal rejected authentication")
                errors["base"] = "invalid_auth"
            except (EbokError, OSError) as err:
                _LOGGER.warning("eBOK config flow could not connect or parse readings: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating eBOK connection")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="eBOK Tarnów", data=user_input, options={CONF_IMPORT_HISTORY: DEFAULT_IMPORT_HISTORY})
        schema = vol.Schema({vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str, vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry):
        return EbokTarnowOptionsFlow()

class EbokTarnowOptionsFlow(config_entries.OptionsFlow):
    """Configure optional historical reading import."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        schema = vol.Schema({vol.Optional(CONF_IMPORT_HISTORY, default=self.config_entry.options.get(CONF_IMPORT_HISTORY, DEFAULT_IMPORT_HISTORY)): bool})
        return self.async_show_form(step_id="init", data_schema=schema)
