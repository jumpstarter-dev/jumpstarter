from dataclasses import dataclass, field
from threading import Condition
from typing import Literal

import paho.mqtt.client as paho
from jumpstarter_driver_power.driver import PowerInterface
from paho.mqtt.enums import CallbackAPIVersion

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class TasmotaPower(PowerInterface, Driver):
    """driver for tasmota compatible power switches"""

    client_id: str | None = None
    transport: Literal["tcp", "websockets", "unix"] = "tcp"
    timeout: float | None = None

    host: str
    port: int = 1883
    tls: bool = True

    username: str | None = None
    password: str | None = None

    cmnd_topic: str
    stat_topic: str

    mq: paho.Client = field(init=False)
    state: str | None = field(init=False, default=None)
    cond: Condition = field(init=False, default_factory=Condition)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self.mq = paho.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            transport=self.transport,
        )

        def on_message(client, userdata, msg):
            if msg.topic == self.stat_topic:
                self.state = msg.payload.decode()
                with self.cond:
                    self.cond.notify_all()

        self.mq.on_message = on_message

        if self.tls:
            self.mq.tls_set()

        self.mq.username_pw_set(self.username, self.password)
        self.mq.connect(self.host, self.port)
        self.mq.loop_start()

        self.mq.subscribe(self.stat_topic)

    def publish(self, state):
        self.mq.publish(
            self.cmnd_topic,
            payload=state,
            qos=1,
        ).wait_for_publish(
            timeout=self.timeout,
        )
        with self.cond:
            self.cond.wait_for(
                lambda: self.state == state,
                timeout=self.timeout,
            )

    @export
    def on(self):
        self.publish("ON")

    @export
    def off(self):
        self.publish("OFF")

    @export
    def read(self):
        pass

    def close(self):
        self.off()

    def reset(self):
        self.off()
