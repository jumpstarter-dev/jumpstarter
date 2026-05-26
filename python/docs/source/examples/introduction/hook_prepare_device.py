import os

from jumpstarter.utils.env import env

lease = os.environ["LEASE_NAME"]
print(f"Preparing device for lease {lease}")

with env() as client:
    client.power.on()
    print("Power on complete")
