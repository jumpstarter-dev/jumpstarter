# Turn device power on
power_client.on()

# Turn device power off
power_client.off()

# Power cycle the device
power_client.cycle(wait=5)  # Wait 5 seconds between off/on
