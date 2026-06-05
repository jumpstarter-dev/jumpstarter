info = client.storage.get_chip_info()
print(info["chip"])      # e.g. "ESP32-D0WD-V3 (revision v3.1)"
print(info["features"])  # e.g. "Wi-Fi, BT, Dual Core"
print(info["mac"])       # e.g. "5c:01:3b:68:ab:0c"

client.storage.flash("/path/to/firmware.bin", target="0x1000")

client.storage.enter_bootloader()

client.storage.erase()

client.storage.hard_reset()

console = client.serial.open()
console.sendline("import machine")
console.expect(">>>")
