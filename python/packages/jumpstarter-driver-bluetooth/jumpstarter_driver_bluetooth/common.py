from pydantic import BaseModel, ConfigDict, field_validator


class BluetoothIdentity(BaseModel):
    """What a device is on the air: the three things another device sees of it before they pair."""

    model_config = ConfigDict(frozen=True)

    name: str
    # The device's own Bluetooth address, as ``AA:BB:CC:DD:EE:FF``. Empty when it is not known: the bridge
    # then cannot call the device, and learns the address when the device calls.
    address: str = ""
    # The 24-bit class of device: what kind of thing it says it is (a phone, a car's audio).
    class_of_device: int = 0x001F00

    @field_validator("address")
    @classmethod
    def _valid_address(cls, address: str) -> str:
        address = address.strip().upper()
        if address and (len(address) != 17 or len(bytes.fromhex(address.replace(":", ""))) != 6):
            raise ValueError(f"'{address}' is not a Bluetooth address (AA:BB:CC:DD:EE:FF)")
        return address

    @field_validator("class_of_device")
    @classmethod
    def _valid_class(cls, value: int) -> int:
        if not 0 <= value < 1 << 24:
            raise ValueError("a class of device has 24 bits")
        return value


# What a Bluetooth driver may be able to do, and what provides each. A driver's ``capabilities`` (in
# ``get_bluetooth_state``) lists those it has; a method of one it has not says the line here.
PROVIDED_BY = {
    "link": "a link driver (BluetoothBridge, BluetoothControllerPort, BluetoothRemoteController)",
    "identity": "a bridge (BluetoothBridge, or Bluetooth with 'link: bridge'), which says who the device is",
    "pairing": "a bridge (BluetoothBridge, or Bluetooth with 'link: bridge'), which sees both pairings",
    "state": "a device view (AndroidBluetooth, or Bluetooth with 'device'), which asks the device's own stack",
    "enable": "a device view (AndroidBluetooth, or Bluetooth with 'device')",
    "bonds": "a device view (AndroidBluetooth, or Bluetooth with 'device')",
    "capture": "a device view (AndroidBluetooth, or Bluetooth with 'device'): the device's own HCI snoop log",
    "packets": "a device view (AndroidBluetooth, or Bluetooth with 'device'): the device's own HCI snoop log",
}
