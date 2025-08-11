"""Integration for Rainforest EMU2 devices."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import EMU2ConfigEntry, EMU2DataCoordinator

PLATFORMS = (Platform.SENSOR,)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the EMU2 component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EMU2ConfigEntry) -> bool:
    """Set up Rainforest EMU2 device from a config entry."""
    coordinator = EMU2DataCoordinator(hass, entry)
    await coordinator.async_open_device()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EMU2ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
