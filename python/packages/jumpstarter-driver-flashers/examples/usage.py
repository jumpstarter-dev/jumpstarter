with flasherclient.busybox_shell() as serial:
    serial.send("ls -la\n")
    serial.expect("#")
    print(serial.before)
