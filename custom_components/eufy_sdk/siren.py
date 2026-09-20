"""
Siren platform — the `siren` capability's dedicated trigger()/stop() actions.

Like `lock`, an alarm is not a writable bool: the SDK exposes it as two momentary
methods, so this bypasses `switch.py`'s property routing and calls `client.action()`.

Which devices get one: every device whose bridge record lists the `siren` capability —
a HomeBase reporting hub-alarm params, a camera attached to one that reports the EAS
slot, or a standalone siren. Only the last kind reports a sounding state (`siren`); for
the other two the entity holds the written value until the duration runs out, the same
optimistic pattern `lock.py` uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .entity import EufySdkDeviceEntity, has_capability

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# `siren.turn_on` may omit a duration, but the SDK rejects anything that is not a
# positive whole number of seconds — so a call without one gets this bounded default
# rather than a sound nobody asked to stop.
DEFAULT_DURATION_SECS = 30

# The alarm stops itself when the duration elapses, and no push reports that. Drop the
# optimistic hold a moment later, so a HomeBase that ran its own timeout still lands on
# the truth rather than a stuck "on".
OPTIMISTIC_GRACE_SECS = 2


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a Siren entity per device that declares the `siren` capability."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EufySdkSiren(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if has_capability(dev, "siren")
    )


class EufySdkSiren(EufySdkDeviceEntity, SirenEntity):
    """A eufy alarm output, driven by the SDK's `trigger`/`stop` actions."""

    _attr_name = "Siren"
    _attr_supported_features = (
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.DURATION
    )

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to an alarm-capable device serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_siren"
        self._expiry_unsub = None
        self._assumed_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """
        The sounding state a standalone siren reports, else the optimistic hold.

        `siren` is installed only for the standalone family, so for a HomeBase or an
        attached camera this is the written value until its duration elapses — and
        `None` before anything has been written, rather than a guessed "off".
        """
        reported = self.device.get("state", {}).get("siren")
        if reported is not None:
            return bool(reported)
        return self._assumed_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Sound the alarm for `duration` seconds (a bounded default without one)."""
        duration = int(kwargs.get("duration") or DEFAULT_DURATION_SECS)
        client = self.coordinator.config_entry.runtime_data.client
        await client.action(self._sn, "trigger", duration)
        self._hold(on=True)
        self._expiry_unsub = async_call_later(
            self.hass, duration + OPTIMISTIC_GRACE_SECS, self._expire
        )

    async def async_turn_off(self, **_: Any) -> None:
        """Silence a sounding alarm before its duration runs out."""
        client = self.coordinator.config_entry.runtime_data.client
        await client.action(self._sn, "stop")
        self._hold(on=False)

    @callback
    def _hold(self, *, on: bool) -> None:
        """Hold the just-written value and cancel any pending expiry."""
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
        self._assumed_on = on
        self.async_write_ha_state()

    @callback
    def _expire(self, _now: Any) -> None:
        """
        Drop the hold once the duration has elapsed — the alarm stopped itself.

        `@callback` is load-bearing: `async_call_later` runs an undecorated function in
        an executor thread, and `async_write_ha_state` must not be called from one.
        """
        self._expiry_unsub = None
        self._assumed_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending expiry when the entity goes away."""
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
        await super().async_will_remove_from_hass()
