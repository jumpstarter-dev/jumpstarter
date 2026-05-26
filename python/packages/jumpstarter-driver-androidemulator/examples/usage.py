from jumpstarter.common.utils import serve
from jumpstarter_driver_androidemulator.driver import AndroidEmulator

driver = AndroidEmulator(avd_name="Pixel_6")
with serve(driver) as client:
    client.power.on()

    # Wait for boot and get an adbutils device
    with client.adb_device(timeout=180) as device:
        print(device.prop.model)
        print(device.shell("pm list packages"))

    client.power.off()
