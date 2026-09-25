"""Local per-camera snapshot acquisition policy."""

from __future__ import annotations

SNAPSHOT_POLICY_DEFAULT = "Default"
SNAPSHOT_POLICY_AUTO = "Auto"
SNAPSHOT_POLICY_STORED = "Stored"
SNAPSHOT_POLICY_LIVE = "Live"

SNAPSHOT_POLICY_OPTIONS = (
    SNAPSHOT_POLICY_DEFAULT,
    SNAPSHOT_POLICY_AUTO,
    SNAPSHOT_POLICY_STORED,
    SNAPSHOT_POLICY_LIVE,
)

_BRIDGE_MODE_BY_POLICY = {
    SNAPSHOT_POLICY_AUTO: "auto",
    SNAPSHOT_POLICY_STORED: "stored",
    SNAPSHOT_POLICY_LIVE: "live",
}


def normalize_snapshot_policy(value: str | None) -> str:
    """Return a supported local policy, defaulting unknown/restored values safely."""
    return value if value in SNAPSHOT_POLICY_OPTIONS else SNAPSHOT_POLICY_DEFAULT


def bridge_mode_for_policy(policy: str | None) -> str | None:
    """Return the bridge query mode, or None to preserve the legacy request."""
    return _BRIDGE_MODE_BY_POLICY.get(normalize_snapshot_policy(policy))


def snapshot_url(host: str, port: int, sn: str, policy: str | None) -> str:
    """Build a snapshot URL while omitting the query for the legacy policy."""
    url = f"http://{host}:{port}/snapshot/{sn}"
    mode = bridge_mode_for_policy(policy)
    return f"{url}?mode={mode}" if mode else url
