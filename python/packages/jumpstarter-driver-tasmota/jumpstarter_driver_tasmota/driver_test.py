import pytest
from pytest_mqtt.model import MqttMessage

from .driver import TasmotaPower
from jumpstarter.common.utils import serve


@pytest.mark.skip("requires docker")
def test_tasmota_power(mosquitto, capmqtt):
    cmnd_topic = "cmnd/tasmota_6990F2/POWER"
    stat_topic = "stat/tasmota_6990F2/POWER"

    with serve(
        TasmotaPower(
            host=mosquitto[0],
            port=int(mosquitto[1]),
            tls=False,
            transport="tcp",
            cmnd_topic=cmnd_topic,
            stat_topic=stat_topic,
        )
    ) as client:
        capmqtt.publish(topic=stat_topic, payload="ON")
        client.on()
        assert MqttMessage(topic=cmnd_topic, payload=b"ON", userdata=None) in capmqtt.messages

        capmqtt.publish(topic=stat_topic, payload="OFF")
        client.off()
        assert MqttMessage(topic=cmnd_topic, payload=b"OFF", userdata=None) in capmqtt.messages
