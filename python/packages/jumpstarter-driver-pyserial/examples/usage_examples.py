session = pyserialclient.open()
session.sendline("Hello, world!")
session.expect("Hello, world!")
pyserialclient.close()
