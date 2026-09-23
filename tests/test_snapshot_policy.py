# ruff: noqa: D100, D102, INP001, PT009

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.eufy_sdk.select import EufySdkSnapshotPolicySelect
from custom_components.eufy_sdk.snapshot_policy import (
    SNAPSHOT_POLICY_AUTO,
    SNAPSHOT_POLICY_DEFAULT,
    SNAPSHOT_POLICY_FRESH,
    SNAPSHOT_POLICY_STORED,
    bridge_mode_for_policy,
    normalize_snapshot_policy,
    snapshot_url,
)


class SnapshotPolicyTests(unittest.IsolatedAsyncioTestCase):
    """Test the local policy-to-bridge contract."""

    def test_default_preserves_bare_snapshot(self) -> None:
        self.assertIsNone(bridge_mode_for_policy(None))
        self.assertIsNone(bridge_mode_for_policy(SNAPSHOT_POLICY_DEFAULT))
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", None),
            "http://bridge:3000/snapshot/CAM1",
        )

    def test_explicit_modes_map_to_bridge_query_values(self) -> None:
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_AUTO), "auto")
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_STORED), "stored")
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_FRESH), "live")
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_AUTO),
            "http://bridge:3000/snapshot/CAM1?mode=auto",
        )
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_STORED),
            "http://bridge:3000/snapshot/CAM1?mode=stored",
        )
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_FRESH),
            "http://bridge:3000/snapshot/CAM1?mode=live",
        )

    def test_unknown_restored_value_defaults_safely(self) -> None:
        self.assertEqual(normalize_snapshot_policy("removed"), SNAPSHOT_POLICY_DEFAULT)

    def test_policies_are_independent_by_camera_key(self) -> None:
        policies = {"camera-a": SNAPSHOT_POLICY_FRESH}
        self.assertEqual(bridge_mode_for_policy(policies.get("camera-a")), "live")
        self.assertIsNone(bridge_mode_for_policy(policies.get("camera-b")))

    async def test_select_update_is_local_and_persists_in_runtime_data(self) -> None:
        entity = object.__new__(EufySdkSnapshotPolicySelect)
        entity._sn = "camera-a"  # noqa: SLF001
        entity._policy = SNAPSHOT_POLICY_DEFAULT  # noqa: SLF001
        entity.coordinator = SimpleNamespace(
            config_entry=SimpleNamespace(
                runtime_data=SimpleNamespace(snapshot_policy={}, client=Mock()),
            ),
        )
        entity.async_write_ha_state = Mock()

        await entity.async_select_option(SNAPSHOT_POLICY_FRESH)

        self.assertEqual(
            entity.coordinator.config_entry.runtime_data.snapshot_policy,
            {"camera-a": SNAPSHOT_POLICY_FRESH},
        )
        entity.async_write_ha_state.assert_called_once_with()
        entity.coordinator.config_entry.runtime_data.client.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
