import asyncio

from jumpstarter_rust import Session

from jumpstarter.common import TemporarySocket
from jumpstarter.drivers.power.driver import MockPower


async def main():
    print("done")
    root_device = MockPower(uuid="d3f08158-7d49-407b-a021-d2e60113b991")

    with Session(root_device=root_device) as session:
        with TemporarySocket() as path:
            print(path)
            await session.serve_unix(str(path))


asyncio.run(main())
