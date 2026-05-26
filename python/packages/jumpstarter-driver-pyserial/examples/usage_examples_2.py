stream = pyserialclient.open_stream()
stream.send(b"Hello, world!")
data = stream.receive()
