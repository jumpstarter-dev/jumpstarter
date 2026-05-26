from jumpstarter.common.utils import env

with env() as client:
    xcp = client.xcp

    info = xcp.connect()
    print(f"Max CTO: {info.max_cto}, Max DTO: {info.max_dto}")

    xcp.unlock()

    data = xcp.upload(4, 0x1000)
    print(f"Memory at 0x1000: {data.hex()}")

    xcp.download(0x2000, b"\x42\x00\x00\x00")

    xcp.disconnect()
