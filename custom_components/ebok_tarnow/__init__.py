from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EbokTarnowCoordinator
from .history import async_import_reading_history

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = EbokTarnowCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        # The sensor platform reads the coordinator from hass.data during setup.
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        # The coordinator owns an aiohttp session even when its first refresh
        # fails. Close it before propagating setup failure so retries don't leak
        # sessions or sockets.
        await coordinator.client.async_close()
        raise

    try:
        imported = await async_import_reading_history(hass, entry, coordinator.client)
        if imported:
            _LOGGER.info("eBOK history import queued: %s points", imported)
    except Exception as err:
        _LOGGER.warning(
            "eBOK historical import failed; live sensors remain active: %s",
            err,
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if coordinator:
            await coordinator.client.async_close()
    return unload_ok
