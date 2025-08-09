"""Data update coordination for Rainforest EMU2 devices."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import timedelta
import logging
import os
import signal
import subprocess
from typing import Any

from aioraven.data import DeviceInfo as RAVEnDeviceInfo
from aioraven.device import RAVEnConnectionError
from aioraven.serial import RAVEnSerialDevice

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_DEVICE_MAC, DOMAIN

type EMU2ConfigEntry = ConfigEntry["EMU2DataCoordinator"]

_LOGGER = logging.getLogger(__name__)


async def _get_meter_data(
    device: RAVEnSerialDevice, meter: bytes
) -> dict[str, dict[str, Any]]:
    data = {}

    sum_info = await device.get_current_summation_delivered(meter=meter)
    demand_info = await device.get_instantaneous_demand(meter=meter)
    price_info = await device.get_current_price(meter=meter)

    if sum_info and sum_info.meter_mac_id == meter:
        data["CurrentSummationDelivered"] = asdict(sum_info)

    if demand_info and demand_info.meter_mac_id == meter:
        data["InstantaneousDemand"] = asdict(demand_info)

    if price_info and price_info.meter_mac_id == meter:
        data["PriceCluster"] = asdict(price_info)

    return data


async def _get_all_data(
    device: RAVEnSerialDevice, meter_macs: list[str]
) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {"Meters": {}}

    for meter_mac in meter_macs:
        data["Meters"][meter_mac] = await _get_meter_data(
            device, bytes.fromhex(meter_mac)
        )

    network_info = await device.get_network_info()

    if network_info and network_info.link_strength:
        data["NetworkInfo"] = asdict(network_info)

    return data


class EMU2DataCoordinator(DataUpdateCoordinator):
    """Communication coordinator for a Rainforest EMU2 device."""

    _raven_device: RAVEnSerialDevice | None = None
    _device_info: RAVEnDeviceInfo | None = None
    config_entry: EMU2ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: EMU2ConfigEntry) -> None:
        """Initialize the data object."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

    @property
    def device_mac_address(self) -> str | None:
        """Return the MAC address of the device."""
        if self._device_info and self._device_info.device_mac_id:
            return self._device_info.device_mac_id.hex()
        return self.config_entry.data.get(CONF_DEVICE_MAC)

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info."""
        mac_address = self.device_mac_address
        if not mac_address:
            return None
        if device_info := self._device_info:
            return DeviceInfo(
                identifiers={(DOMAIN, mac_address)},
                manufacturer=device_info.manufacturer,
                model=device_info.model_id,
                model_id=device_info.model_id,
                name="Rainforest EMU2",
                sw_version=device_info.fw_version,
                hw_version=device_info.hw_version,
            )
        return DeviceInfo(identifiers={(DOMAIN, mac_address)}, name="Rainforest EMU2")

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        await self._cleanup_device()
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            device = await self._get_device()
            async with asyncio.timeout(30):
                return await _get_all_data(device, self.config_entry.data[CONF_MAC])
        except RAVEnConnectionError as err:
            await self._cleanup_device()
            raise UpdateFailed(f"RAVEnConnectionError: {err}") from err
        except TimeoutError:
            _LOGGER.warning("Timeout while updating data; using last known values")
            return self.data

    async def _cleanup_device(self) -> None:
        device, self._raven_device = self._raven_device, None
        if device is not None:
            await device.close()

    async def _get_device(self) -> RAVEnSerialDevice:
        if self._raven_device is not None:
            return self._raven_device

        await self._kill_serial_hooks()

        device = RAVEnSerialDevice(self.config_entry.data[CONF_DEVICE])

        try:
            async with asyncio.timeout(20):
                await device.open()
                await device.synchronize()
                self._device_info = await device.get_device_info()
        except TimeoutError:
            _LOGGER.warning("Timeout opening device; assuming device is ready")
        except Exception:
            await device.abort()
            raise

        self._raven_device = device
        return device

    async def _kill_serial_hooks(self) -> None:
        """Terminate other processes using the serial device."""
        dev_path = self.config_entry.data[CONF_DEVICE]

        def _kill() -> None:
            try:
                subprocess.run(
                    ["fuser", "-k", dev_path],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                try:
                    pids = subprocess.check_output(["lsof", "-t", dev_path]).split()
                except (FileNotFoundError, subprocess.CalledProcessError):
                    return
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except OSError:
                        continue

        await self.hass.async_add_executor_job(_kill)

    async def async_open_device(self) -> None:
        """Ensure the serial device is opened when the integration loads."""
        await self._get_device()
