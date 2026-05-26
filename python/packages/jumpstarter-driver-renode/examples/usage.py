from jumpstarter_driver_renode.driver import Renode
from jumpstarter.common.utils import serve

with serve(
    Renode(
        platform="platforms/boards/stm32f4_discovery-kit.repl",
        uart="sysbus.usart2",
    )
) as renode:
    renode.flasher.flash("/path/to/firmware.elf")
    renode.power.on()

    with renode.console.pexpect() as p:
        p.expect("Hello from MCU", timeout=30)

    renode.power.off()
