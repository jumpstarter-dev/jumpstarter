# What a Wi-Fi driver may be able to do, and what provides each. A driver's ``capabilities`` (in
# ``get_wifi_state``) lists those it has; a method of one it has not says the line here.
PROVIDED_BY = {
    "link": "a link driver (WifiMedium, WifiGateway)",
    "state": "a device view, which asks the device's own Wi-Fi (planned: none is written yet)",
    "capture": "planned: a pcapng of the frames that cross a WifiMedium, or tcpdump on the device",
    "packets": "planned: a pcapng of the frames that cross a WifiMedium, or tcpdump on the device",
}
