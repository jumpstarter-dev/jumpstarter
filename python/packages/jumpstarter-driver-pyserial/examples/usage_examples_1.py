with pyserialclient.stream() as stream:
    stream.send(b"Hello, world!")
    data = stream.receive()
