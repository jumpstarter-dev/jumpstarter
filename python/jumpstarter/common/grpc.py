import grpc


async def insecure_channel(target, options=None, compression=None):
    return grpc.aio.insecure_channel(target, options, compression)


async def secure_channel(target, credentials, options=None, compression=None):
    return grpc.aio.secure_channel(target, credentials, options, compression)
