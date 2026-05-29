import time
from jumpstarter.common.utils import env

with env() as client:
   client.power.on()
   client.power.off()
