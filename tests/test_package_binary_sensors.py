# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from custom_components.eufy_sdk import binary_sensor, device_trigger, event
from custom_components.eufy_sdk.event import EufySdkDetectionEvent
from custom_components.eufy_sdk.pushmap import (
    DETECTION_EVENTS,
    PUSH_AUTO_OFF_SECONDS,
)

SN = "T821400000000001"
PACKAGE_EVENTS = {
    "packageDelivered": ("package_delivered", "Package delivered"),
    "packageTaken": ("package_taken", "Package taken"),
    "packageStranded": ("package_stranded", "Package stranded"),
}


def entry_with(devices: dict[str, dict]):
    """Build an entry with the device records used by binary-sensor setup."""
    coordinator = Mock(data=devices)
    coordinator.solix_devices = {}
    runtime_data = SimpleNamespace(coordinator=coordinator, properties={})
    return SimpleNamespace(runtime_data=runtime_data), coordinator


class PackagePushBinarySensorTests(unittest.IsolatedAsyncioTestCase):
    async def test_package_events_create_only_the_matching_sensor_and_auto_off(self):
        entry, _coordinator = entry_with({SN: {"capabilities": ["doorbell", "motion"]}})
        entities = []

        await binary_sensor.async_setup_entry(None, entry, entities.extend)

        package_sensors = {
            next(iter(entity._events)): entity
            for entity in entities
            if isinstance(entity, binary_sensor.EufyPushBinarySensor)
            and any(event in PACKAGE_EVENTS for event in entity._events)
        }
        self.assertEqual(set(package_sensors), set(PACKAGE_EVENTS))
        motion_sensor = next(
            entity for entity in entities if entity.unique_id == f"{SN}_motion"
        )
        motion_sensor.hass = Mock()
        motion_sensor.async_write_ha_state = Mock()
        for event_name, (key, name) in PACKAGE_EVENTS.items():
            sensor = package_sensors[event_name]
            self.assertEqual(sensor.unique_id, f"{SN}_{key}")
            self.assertEqual(sensor.name, name)
            self.assertIsNone(sensor.device_class)
            self.assertFalse(sensor.is_on)

            timer_cancel = Mock()
            sensor.hass = Mock()
            sensor.async_write_ha_state = Mock()
            event = SimpleNamespace(data={"deviceSn": SN, "event": event_name})
            motion_sensor._handle_event(event)
            self.assertFalse(motion_sensor.is_on)
            with patch.object(
                binary_sensor,
                "async_call_later",
                return_value=timer_cancel,
            ) as call_later:
                sensor._handle_event(event)

            self.assertTrue(sensor.is_on)
            self.assertEqual(call_later.call_args.args[1], PUSH_AUTO_OFF_SECONDS)
            call_later.assert_called_once()
            for other_event, other_sensor in package_sensors.items():
                if other_event != event_name:
                    self.assertFalse(other_sensor.is_on)

            call_later.call_args.args[2](None)
            self.assertFalse(sensor.is_on)
            sensor.async_write_ha_state.assert_has_calls([call(), call()])
            timer_cancel.assert_not_called()

    async def test_package_sensors_use_existing_doorbell_capability_gate(self):
        entry, _coordinator = entry_with(
            {
                SN: {"capabilities": ["doorbell"]},
                "camera-without-doorbell": {"capabilities": ["motion"]},
            }
        )
        entities = []

        await binary_sensor.async_setup_entry(None, entry, entities.extend)

        package_entities = [
            entity
            for entity in entities
            if entity.unique_id.endswith(
                ("_package_delivered", "_package_taken", "_package_stranded")
            )
        ]
        self.assertEqual(len(package_entities), 3)
        self.assertTrue(all(entity._sn == SN for entity in package_entities))

    async def test_event_sensors_coexist_with_upstream_package_state_sensor(self):
        entry, _coordinator = entry_with({SN: {"capabilities": ["doorbell"]}})
        entities = []

        await binary_sensor.async_setup_entry(None, entry, entities.extend)

        package_state = next(
            entity
            for entity in entities
            if isinstance(entity, binary_sensor.EufyPackageBinarySensor)
        )
        delivered = next(
            entity
            for entity in entities
            if entity.unique_id == f"{SN}_package_delivered"
        )
        taken = next(
            entity for entity in entities if entity.unique_id == f"{SN}_package_taken"
        )
        stranded = next(
            entity
            for entity in entities
            if entity.unique_id == f"{SN}_package_stranded"
        )
        self.assertNotEqual(package_state.unique_id, delivered.unique_id)
        self.assertNotEqual(package_state.unique_id, taken.unique_id)
        self.assertNotEqual(package_state.unique_id, stranded.unique_id)
        self.assertEqual(delivered.name, "Package delivered")
        self.assertEqual(taken.name, "Package taken")
        self.assertEqual(stranded.name, "Package stranded")
        self.assertEqual(delivered.device_info, package_state.device_info)

        for sensor in (package_state, delivered, taken):
            sensor.async_write_ha_state = Mock()
        delivered.hass = Mock()
        taken.hass = Mock()

        delivered_event = SimpleNamespace(
            data={"deviceSn": SN, "event": "packageDelivered"}
        )
        taken_event = SimpleNamespace(data={"deviceSn": SN, "event": "packageTaken"})
        with patch.object(binary_sensor, "async_call_later", return_value=Mock()):
            delivered._handle_event(delivered_event)
            package_state._handle_event(delivered_event)
            taken._handle_event(taken_event)
            package_state._handle_event(taken_event)
            self.assertTrue(delivered.is_on)
            self.assertTrue(taken.is_on)
            self.assertFalse(package_state.is_on)

    async def test_pulse_rearm_and_reset_do_not_change_persistent_package_state(self):
        entry, _coordinator = entry_with({SN: {"capabilities": ["doorbell"]}})
        entities = []
        await binary_sensor.async_setup_entry(None, entry, entities.extend)
        sensors = {sensor.unique_id: sensor for sensor in entities}
        package_state = sensors[f"{SN}_package"]
        delivered = sensors[f"{SN}_package_delivered"]
        stranded = sensors[f"{SN}_package_stranded"]
        taken = sensors[f"{SN}_package_taken"]
        for sensor in (package_state, delivered, stranded, taken):
            sensor.async_write_ha_state = Mock()
        for sensor in (delivered, stranded, taken):
            sensor.hass = Mock()

        callbacks: list[Callable[[object], None]] = []
        cancel_handles = [Mock() for _ in range(4)]

        def schedule_auto_off(
            _hass: object, delay: float, callback: Callable[[object], None]
        ) -> Mock:
            self.assertEqual(delay, PUSH_AUTO_OFF_SECONDS)
            callbacks.append(callback)
            return cancel_handles[len(callbacks) - 1]

        delivered_event = SimpleNamespace(
            data={"deviceSn": SN, "event": "packageDelivered"}
        )
        stranded_event = SimpleNamespace(
            data={"deviceSn": SN, "event": "packageStranded"}
        )
        taken_event = SimpleNamespace(data={"deviceSn": SN, "event": "packageTaken"})
        with patch.object(
            binary_sensor, "async_call_later", side_effect=schedule_auto_off
        ) as schedule:
            delivered._handle_event(delivered_event)
            package_state._handle_event(delivered_event)
            delivered._handle_event(delivered_event)
            package_state._handle_event(delivered_event)
            stranded._handle_event(stranded_event)
            package_state._handle_event(stranded_event)
            self.assertTrue(package_state.is_on)
            self.assertTrue(delivered.is_on)
            self.assertTrue(stranded.is_on)

            callbacks[1](None)
            self.assertFalse(delivered.is_on)
            self.assertTrue(package_state.is_on)
            self.assertTrue(stranded.is_on)

            taken._handle_event(taken_event)
            package_state._handle_event(taken_event)
            self.assertFalse(package_state.is_on)
            self.assertFalse(delivered.is_on)
            self.assertTrue(stranded.is_on)
            self.assertTrue(taken.is_on)
            self.assertEqual(schedule.call_count, 4)
            cancel_handles[0].assert_called_once()

            callbacks[2](None)
            self.assertFalse(stranded.is_on)
            self.assertFalse(package_state.is_on)
            callbacks[3](None)
            self.assertFalse(taken.is_on)
            self.assertFalse(package_state.is_on)

    async def test_motion_and_person_push_sensors_keep_their_existing_behavior(self):
        entry, _coordinator = entry_with(
            {SN: {"capabilities": ["motion", "person_detection"]}}
        )
        entities = []

        await binary_sensor.async_setup_entry(None, entry, entities.extend)

        sensors = {entity.unique_id: entity for entity in entities}
        motion = sensors[f"{SN}_motion"]
        person = sensors[f"{SN}_person"]
        motion.hass = Mock()
        person.hass = Mock()
        motion.async_write_ha_state = Mock()
        person.async_write_ha_state = Mock()
        motion_event = SimpleNamespace(data={"deviceSn": SN, "event": "motion"})
        person_event = SimpleNamespace(data={"deviceSn": SN, "event": "personDetected"})

        with patch.object(binary_sensor, "async_call_later", return_value=Mock()):
            motion._handle_event(person_event)
            self.assertTrue(motion.is_on)  # umbrella still includes visual detections
            self.assertFalse(person.is_on)
            person._handle_event(person_event)
            self.assertTrue(person.is_on)
            motion._handle_event(motion_event)
            self.assertTrue(motion.is_on)

    async def test_detection_entity_and_package_device_triggers_are_unchanged(self):
        entry, _coordinator = entry_with({SN: {"capabilities": ["doorbell"]}})
        entities = []

        await event.async_setup_entry(None, entry, entities.extend)
        detection = next(
            entity for entity in entities if isinstance(entity, EufySdkDetectionEvent)
        )
        self.assertEqual(set(detection.event_types), set(DETECTION_EVENTS.values()))
        for event_name, event_type in (
            ("packageDelivered", "package_delivered"),
            ("packageTaken", "package_taken"),
            ("packageStranded", "package_stranded"),
        ):
            self.assertEqual(DETECTION_EVENTS[event_name], event_type)
        for trigger_type, event_name in (
            ("package_delivered", "packageDelivered"),
            ("package_taken", "packageTaken"),
            ("package_stranded", "packageStranded"),
        ):
            self.assertEqual(device_trigger.TRIGGER_MAP[trigger_type][0], event_name)
            self.assertEqual(device_trigger.TRIGGER_MAP[trigger_type][1], "doorbell")


if __name__ == "__main__":
    unittest.main()
