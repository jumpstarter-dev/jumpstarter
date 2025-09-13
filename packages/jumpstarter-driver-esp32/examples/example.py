#!/usr/bin/env python3
import os
from jumpstarter.common.utils import env
from jumpstarter.config.client import ClientConfigV1Alpha1

def main():
    with env() as client:
        esp32 = client.esp32

        info = esp32.chip_info()
        print(f"Connected to {info['chip_revision']}")
        print(f"MAC Address: {info['mac_address']}")


if __name__ == "__main__":
    main()
