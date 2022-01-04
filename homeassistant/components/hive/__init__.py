"""Support for the Hive devices and services."""
from functools import wraps
import logging

from aiohttp.web_exceptions import HTTPException
from apyhiveapi import Hive
from apyhiveapi.helper.hive_exceptions import HiveReauthRequired
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    POWER_WATT,
    TEMP_CELSIUS,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client, config_validation as cv
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORM_LOOKUP, PLATFORMS

DEVICETYPE = {
    "contactsensor": {"device_class": BinarySensorDeviceClass.OPENING},
    "motionsensor": {"device_class": BinarySensorDeviceClass.MOTION},
    "Connectivity": {"device_class": BinarySensorDeviceClass.CONNECTIVITY},
    "SMOKE_CO": {"device_class": BinarySensorDeviceClass.SMOKE},
    "DOG_BARK": {"device_class": BinarySensorDeviceClass.SOUND},
    "GLASS_BREAK": {"device_class": BinarySensorDeviceClass.SOUND},
    "Heating_Current_Temperature": {
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": TEMP_CELSIUS,
    },
    "Heating_Target_Temperature": {
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": TEMP_CELSIUS,
    },
    "Heating_State": {"icon": "mdi:radiator"},
    "Heating_Mode": {"icon": "mdi:radiator"},
    "Heating_Boost": {"icon": "mdi:radiator"},
    "Hotwater_State": {"icon": "mdi:water-pump"},
    "Hotwater_Mode": {"icon": "mdi:water-pump"},
    "Hotwater_Boost": {"icon": "mdi:water-pump"},
    "Heating_Heat_On_Demand": {},
    "Mode": {
        "icon": "mdi:eye",
    },
    "Availability": {"icon": "mdi:check-circle"},
    "warmwhitelight": {},
    "tuneablelight": {},
    "colourtunablelight": {},
    "activeplug": {},
    "trvcontrol": {},
    "heating": {},
    "siren": {},
    "Battery": {
        "device_class": SensorDeviceClass.BATTERY,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "Power": {
        "device_class": SensorDeviceClass.ENERGY,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": POWER_WATT,
    },
}

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    vol.All(
        cv.deprecated(DOMAIN),
        {
            DOMAIN: vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): cv.string,
                    vol.Required(CONF_USERNAME): cv.string,
                    vol.Optional(CONF_SCAN_INTERVAL, default=2): cv.positive_int,
                },
            )
        },
    ),
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Hive configuration setup."""
    hass.data[DOMAIN] = {}

    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]

    if not hass.config_entries.async_entries(DOMAIN):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data={
                    CONF_USERNAME: conf[CONF_USERNAME],
                    CONF_PASSWORD: conf[CONF_PASSWORD],
                },
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hive from a config entry."""

    websession = aiohttp_client.async_get_clientsession(hass)
    hive = Hive(websession)
    hive_config = dict(entry.data)

    hive_config["options"] = {}
    hive_config["options"].update(
        {CONF_SCAN_INTERVAL: dict(entry.options).get(CONF_SCAN_INTERVAL, 120)}
    )
    hass.data[DOMAIN][entry.entry_id] = hive

    try:
        devices = await hive.session.startSession(hive_config)
    except HTTPException as error:
        _LOGGER.error("Could not connect to the internet: %s", error)
        raise ConfigEntryNotReady() from error
    except HiveReauthRequired as err:
        raise ConfigEntryAuthFailed from err

    for ha_type, hive_type in PLATFORM_LOOKUP.items():
        device_list = devices.get(hive_type)
        if device_list:
            hass.async_create_task(
                hass.config_entries.async_forward_entry_setup(entry, ha_type)
            )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


def refresh_system(func):
    """Force update all entities after state change."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        await func(self, *args, **kwargs)
        async_dispatcher_send(self.hass, DOMAIN)

    return wrapper


class HiveEntity(Entity):
    """Initiate Hive Base Class."""

    def __init__(self, hive, hive_device):
        """Initialize the instance."""
        self.hive = hive
        self.device = hive_device
        self._attr_name = self.device["haName"]
        self._attr_unique_id = f'{self.device["hiveID"]}-{self.device["hiveType"]}'
        self._attr_entity_category = self.device.get("category")
        self._attr_device_class = DEVICETYPE.get(self.device["hiveType"])
        self._attr_state_class = DEVICETYPE[self.device.get("hiveType")].get(
            "state_class"
        )
        self._attr_native_unit_of_measurement = DEVICETYPE[
            self.device.get("hiveType")
        ].get("unit")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device["device_id"])},
            model=self.device["deviceData"]["model"],
            manufacturer=self.device["deviceData"]["manufacturer"],
            name=self.device["device_name"],
            sw_version=self.device["deviceData"]["version"],
            via_device=(DOMAIN, self.device["parentDevice"]),
        )
        self.attributes = {}

    async def async_added_to_hass(self):
        """When entity is added to Home Assistant."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, DOMAIN, self.async_write_ha_state)
        )
