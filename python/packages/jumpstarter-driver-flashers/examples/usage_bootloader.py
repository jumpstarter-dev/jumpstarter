with flasherclient.bootloader_shell() as serial:
    serial.send("version\n")
    serial.expect("=>")
    print(serial.before)
