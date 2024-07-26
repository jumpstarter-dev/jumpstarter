import grpc


async def insecure_channel(target, options=None, compression=None):
    return grpc.aio.insecure_channel(target, options, compression)
