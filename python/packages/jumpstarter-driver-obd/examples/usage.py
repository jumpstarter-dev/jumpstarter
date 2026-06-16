from jumpstarter_driver_obd import OBDConnectionStatus

# Check connection state
status = obd.status()                              # returns OBDConnectionStatus
print(status == OBDConnectionStatus.CAR_CONNECTED) # True
print(obd.is_connected())                          # True

# Discover what the ECU supports
cmds = obd.supported_commands()   # ["RPM", "SPEED", "COOLANT_TEMP", ...]

# Query individual PIDs by name
rpm   = obd.query("RPM")          # "3000.0 revolutions_per_minute"
speed = obd.query("SPEED")        # "60.0 kph"
temp  = obd.query("COOLANT_TEMP") # "90.0 degC"

# Clearing trouble codes is a separate, explicit call (see note below)
obd.clear_dtc()
