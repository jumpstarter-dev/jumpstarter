import json
import subprocess
from base64 import b64encode
from dataclasses import dataclass

import requests

from jumpstarter.driver import Driver, export


class NetsimError(Exception):
    """Raised when a netsim API call fails."""


VALID_STATES = frozenset(("true", "1", "on", "false", "0", "off"))
ON_STATES = frozenset(("true", "1", "on"))
RADIO_TYPES = ("bt_classic", "ble", "wifi", "uwb")


def _parse_bool(value: str, param_name: str) -> bool:
    v = value.lower()
    if v not in VALID_STATES:
        raise NetsimError(f"invalid {param_name}: {value!r}. Use on/off, true/false, or 1/0")
    return v in ON_STATES


@dataclass(kw_only=True)
class Netsim(Driver):
    """Android netsim driver for controlling virtual radio interfaces (Bluetooth, WiFi, UWB)."""

    host: str = "localhost"
    # netsimd web port = 7681 + netsim_instance_num. The instance number
    # depends on CVD numbering and isn't controllable. Port must be discovered
    # or configured per deployment.
    port: int = 7681
    # Path to netsim CLI binary. Required for capture toggle. REST PATCH
    # is broken in netsim <=0.3.100. Empty string = REST only (capture
    # toggle will raise). Typical: /usr/lib/cuttlefish-common/bin/netsim
    netsim_cli: str = ""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_netsim.client.NetsimClient"

    @property
    def _base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _fmt(self, result) -> str:
        return json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)

    def _request(self, method: str, path: str, data: dict | None = None, timeout: int = 10) -> dict | list | str:
        try:
            r = requests.request(method, f"{self._base_url}{path}", json=data, timeout=timeout)
            r.raise_for_status()
            try:
                return r.json()
            except requests.JSONDecodeError:
                return r.text
        except requests.ConnectionError as e:
            raise NetsimError(f"not connected to netsim at {self.host}:{self.port}") from e
        except requests.Timeout as e:
            raise NetsimError(f"{method} {path} timed out after {timeout}s") from e
        except requests.HTTPError as e:
            raise NetsimError(f"{method} {path} failed: {e}") from e

    def _request_bytes(self, path: str, timeout: int = 30) -> bytes:
        try:
            r = requests.get(f"{self._base_url}{path}", timeout=timeout)
            r.raise_for_status()
            return r.content
        except requests.ConnectionError as e:
            raise NetsimError(f"not connected to netsim at {self.host}:{self.port}") from e
        except requests.Timeout as e:
            raise NetsimError(f"GET {path} timed out after {timeout}s") from e
        except requests.HTTPError as e:
            raise NetsimError(f"GET {path} failed: {e}") from e

    def _list_devices_raw(self) -> list[dict]:
        result = self._request("GET", "/v1/devices")
        if isinstance(result, dict):
            return result.get("devices", [])
        return []

    def _resolve_device_id(self, name_or_id: str) -> int:
        """Resolve a device name or numeric ID to a numeric ID.

        Accepts: numeric string ("1"), exact name match, or substring match.
        Raises NetsimError if no match or multiple substring matches.
        """
        if name_or_id.isdigit():
            return int(name_or_id)

        devices = self._list_devices_raw()
        exact = [d for d in devices if d.get("name") == name_or_id]
        if len(exact) == 1:
            return exact[0]["id"]

        substr = [d for d in devices if name_or_id in d.get("name", "")]
        if len(substr) == 1:
            return substr[0]["id"]
        if len(substr) > 1:
            names = [d["name"] for d in substr]
            raise NetsimError(f"ambiguous device {name_or_id!r}, matches: {names}")

        available = [d.get("name", str(d.get("id"))) for d in devices]
        raise NetsimError(f"device not found: {name_or_id!r}. Available: {available}")

    def _capture_toggle(self, capture_id: str, state: str) -> None:
        """Toggle capture state. Uses CLI when configured, raises if not.

        REST PATCH for captures is broken in netsim <=0.3.100.
        """
        if not capture_id.isdigit():
            raise NetsimError(f"capture_id must be numeric, got {capture_id!r}")
        if state not in ("on", "off"):
            raise NetsimError(f"state must be 'on' or 'off', got {state!r}")
        if not self.netsim_cli:
            raise NetsimError(
                "capture toggle requires netsim_cli config "
                "(REST PATCH broken in netsim <=0.3.100). "
                "Set netsim_cli to the netsim binary path, "
                "e.g. /usr/lib/cuttlefish-common/bin/netsim"
            )
        self.logger.info("capture toggle via CLI: %s %s (cli=%s)", state, capture_id, self.netsim_cli)
        try:
            result = subprocess.run(
                [self.netsim_cli, "capture", "patch", state, capture_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as e:
            raise NetsimError("netsim capture patch timed out after 10s") from e
        except OSError as e:
            raise NetsimError(f"failed to run netsim CLI ({self.netsim_cli!r}): {e}") from e
        if result.returncode != 0:
            raise NetsimError(f"netsim capture patch failed: {result.stderr.strip()}")

    def _build_chip_patch(self, radio: str, **fields) -> list[dict]:
        if radio not in RADIO_TYPES:
            raise NetsimError(f"unknown radio: {radio}. Valid: {', '.join(RADIO_TYPES)}")
        # bt_classic and ble both map to chip kind BLUETOOTH - they're sub-fields
        # of the same chip, not separate chips.
        if radio == "bt_classic":
            return [{"kind": "BLUETOOTH", "bt": {"classic": fields}}]
        if radio == "ble":
            return [{"kind": "BLUETOOTH", "bt": {"low_energy": fields}}]
        if radio == "wifi":
            return [{"kind": "WIFI", "wifi": fields}]
        return [{"kind": "UWB", "uwb": fields}]

    @export
    def list_devices(self) -> str:
        """List all devices and their chips/radio states."""
        return self._fmt(self._request("GET", "/v1/devices"))

    @export
    def get_device(self, name_or_id: str) -> str:
        """Get a single device by name or ID."""
        device_id = self._resolve_device_id(name_or_id)
        for dev in self._list_devices_raw():
            if dev.get("id") == device_id:
                return self._fmt(dev)
        raise NetsimError(f"device not found: {name_or_id}")

    @export
    def patch_device(self, name_or_id: str, patch_json: str) -> str:
        """Send raw patch to a device. patch_json is the device object fields to update."""
        try:
            data = json.loads(patch_json)
        except json.JSONDecodeError as e:
            raise NetsimError(f"invalid JSON: {e}") from e
        device_id = self._resolve_device_id(name_or_id)
        return self._fmt(self._request("PATCH", f"/v1/devices/{device_id}", {"device": data}))

    @export
    def set_radio(self, name_or_id: str, radio: str, enabled: str) -> str:
        """Toggle a radio on/off. radio: bt_classic, ble, wifi, uwb. enabled: on/off."""
        state = _parse_bool(enabled, "enabled")
        chips = self._build_chip_patch(radio, state=state)
        device_id = self._resolve_device_id(name_or_id)
        return self._fmt(self._request("PATCH", f"/v1/devices/{device_id}", {"device": {"chips": chips}}))

    @export
    def reset_devices(self) -> str:
        """Reset all devices to initial state.

        WARNING: this is host-wide - resets radios for ALL devices on this
        netsim instance, not just yours. On a shared host, this affects
        other tenants.
        """
        self.logger.warning("reset_devices is host-wide - all devices on this netsim instance will be reset")
        self._request("POST", "/v1/devices/reset")
        return "OK"

    @export
    def list_captures(self) -> str:
        """List all packet captures."""
        return self._fmt(self._request("GET", "/v1/captures"))

    @export
    def get_capture(self, capture_id: str) -> str:
        """Download a packet capture as base64-encoded pcap data.

        Returns base64 string (RPC cannot transport raw bytes safely).
        """
        if not capture_id.isdigit():
            raise NetsimError(f"capture_id must be numeric, got {capture_id!r}")
        data = self._request_bytes(f"/v1/captures/{capture_id}")
        return b64encode(data).decode("ascii")

    @export
    def set_capture(self, capture_id: str, enabled: str) -> str:
        """Start or stop a packet capture by capture ID. enabled: on/off.

        Requires netsim_cli config. REST PATCH broken in netsim <=0.3.100.
        """
        state = "on" if _parse_bool(enabled, "enabled") else "off"
        self._capture_toggle(capture_id, state)
        return "OK"

    @export
    def start_capture(self, name_or_id: str, chip_kind: str = "BLUETOOTH") -> str:
        """Start packet capture for a device, resolved by chip kind.

        Finds the capture entry matching this device and chip kind,
        then enables it via CLI. Returns the capture ID used.
        Requires netsim_cli config.
        """
        if not self.netsim_cli:
            raise NetsimError(
                "capture toggle requires netsim_cli config "
                "(REST PATCH broken in netsim <=0.3.100). "
                "Set netsim_cli to the netsim binary path, "
                "e.g. /usr/lib/cuttlefish-common/bin/netsim"
            )
        device_id = self._resolve_device_id(name_or_id)
        devices = self._list_devices_raw()
        device_name = None
        for dev in devices:
            if dev.get("id") == device_id:
                device_name = dev.get("name")
                break
        if device_name is None:
            raise NetsimError(f"device not found: {name_or_id}")

        captures = self._request("GET", "/v1/captures")
        if not isinstance(captures, dict):
            raise NetsimError("unexpected captures response")
        for cap in captures.get("captures", []):
            if cap.get("deviceName") == device_name and cap.get("chipKind") == chip_kind:
                cap_id = str(cap["id"])
                self._capture_toggle(cap_id, "on")
                return cap_id
        raise NetsimError(f"no {chip_kind} capture found for device {device_name!r}")

    @export
    def stop_capture(self, capture_id: str) -> str:
        """Stop a packet capture. Requires netsim_cli config."""
        self._capture_toggle(capture_id, "off")
        return "OK"

    @export
    def status(self) -> str:
        """Health check — verifies netsim is reachable. Returns device count."""
        devices = self._list_devices_raw()
        return f"OK ({len(devices)} devices)"
