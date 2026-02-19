"""
Custom addon: Real-time data stream WebSocket simulation.

Simulates a WebSocket endpoint that pushes live sensor/telemetry data
to the DUT. This exercises real-time data rendering and update logic
without needing a real data source.

Mock config entry::

    "WEBSOCKET /api/v1/data/realtime": {
        "addon": "data_stream_websocket",
        "addon_config": {
            "push_interval_ms": 100,
            "scenario": "normal",
            "scenarios": {
                "idle": {
                    "value_range": [0, 0],
                    "rate_range": [0, 0],
                    "drain_pct_per_s": 0.001
                },
                "normal": {
                    "value_range": [30, 70],
                    "rate_range": [100, 500],
                    "drain_pct_per_s": 0.015
                },
                "variable": {
                    "value_range": [0, 55],
                    "rate_range": [50, 1000],
                    "drain_pct_per_s": 0.02
                },
                "recovery": {
                    "value_range": [5, 40],
                    "rate_range": [0, 200],
                    "drain_pct_per_s": -0.03
                }
            }
        }
    }

The addon intercepts the initial WebSocket handshake and, once
established, periodically injects telemetry messages to the client
using mitmproxy's ``inject.websocket`` command.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import time

from mitmproxy import ctx, http


class Handler:
    """Sensor data WebSocket mock handler.

    On WebSocket connect, starts an async task that pushes JSON
    telemetry frames to the client at the configured interval.

    Each frame looks like::

        {
            "type": "telemetry",
            "timestamp": 1708300000.123,
            "sensor_value": 52.3,
            "rate": 350,
            "battery_pct": 84.7,
            "voltage": 3.82,
            "state": "active",
            "counter": 12450,
            "temperature_c": 42.1,
            "gps": {"lat": 0.0, "lon": 0.0, "heading": 0.0}
        }
    """

    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    def handle(self, flow: http.HTTPFlow, config: dict) -> bool:
        """Handle the initial WebSocket upgrade request.

        We let the upgrade proceed (so mitmproxy establishes the
        WebSocket), then the websocket_message hook and async
        injector take over.

        Returns True to indicate the request was handled (but we
        don't set flow.response — we let the WebSocket handshake
        complete naturally by NOT intercepting it here).
        """
        # Don't block the handshake — return False to let it through
        # to the server (or get intercepted later by websocket hooks)
        return False

    def websocket_message(self, flow: http.HTTPFlow, config: dict):
        """Handle WebSocket messages and start telemetry injection.

        On the first client message (typically a subscribe/init
        message), start the async telemetry push task.
        """
        if flow.websocket is None:
            return

        last_msg = flow.websocket.messages[-1]

        # Only react to client messages
        if not last_msg.from_client:
            return

        # Parse client command
        try:
            cmd = json.loads(last_msg.text) if last_msg.is_text else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            cmd = {}

        flow_id = id(flow)

        msg_type = cmd.get("type", cmd.get("action", "subscribe"))

        if msg_type in ("subscribe", "start", "init"):
            # Start pushing telemetry if not already running
            if flow_id not in self._tasks or self._tasks[flow_id].done():
                scenario_name = cmd.get(
                    "scenario",
                    config.get("scenario", "normal"),
                )
                interval_ms = config.get("push_interval_ms", 100)
                scenarios = config.get("scenarios", DEFAULT_SCENARIOS)
                scenario = scenarios.get(scenario_name, scenarios.get(
                    "normal", DEFAULT_SCENARIOS["normal"],
                ))

                task = asyncio.ensure_future(
                    self._push_telemetry(
                        flow, scenario, interval_ms / 1000.0,
                    )
                )
                self._tasks[flow_id] = task

                # Send acknowledgment
                ack = json.dumps({
                    "type": "subscribed",
                    "scenario": scenario_name,
                    "interval_ms": interval_ms,
                })
                ctx.master.commands.call(
                    "inject.websocket", flow, True, ack.encode(),
                )
                ctx.log.info(
                    f"WS telemetry started: scenario={scenario_name}, "
                    f"interval={interval_ms}ms"
                )

        elif msg_type in ("unsubscribe", "stop"):
            if flow_id in self._tasks:
                self._tasks[flow_id].cancel()
                del self._tasks[flow_id]
                ctx.log.info("WS telemetry stopped")

        elif msg_type == "set_scenario":
            # Switch scenario mid-stream
            new_scenario = cmd.get("scenario", "normal")
            if flow_id in self._tasks:
                self._tasks[flow_id].cancel()
            scenarios = config.get("scenarios", DEFAULT_SCENARIOS)
            scenario = scenarios.get(new_scenario, DEFAULT_SCENARIOS.get(
                new_scenario, DEFAULT_SCENARIOS["normal"],
            ))
            interval_ms = config.get("push_interval_ms", 100)
            task = asyncio.ensure_future(
                self._push_telemetry(
                    flow, scenario, interval_ms / 1000.0,
                )
            )
            self._tasks[flow_id] = task
            ctx.log.info(f"WS telemetry scenario changed: {new_scenario}")

    async def _push_telemetry(
        self,
        flow: http.HTTPFlow,
        scenario: dict,
        interval_s: float,
    ):
        """Async loop that pushes telemetry frames to the client."""
        state = SensorState(scenario)

        try:
            while (
                flow.websocket is not None
                and flow.websocket.timestamp_end is None
            ):
                frame = state.next_frame()
                payload = json.dumps(frame).encode()

                ctx.master.commands.call(
                    "inject.websocket", flow, True, payload,
                )

                await asyncio.sleep(interval_s)

        except asyncio.CancelledError:
            ctx.log.debug("Telemetry push task cancelled")
        except Exception as e:
            ctx.log.error(f"Telemetry push error: {e}")


class SensorState:
    """Generates simulated sensor telemetry data.

    Uses simple simulation to produce correlated values:
    - Sensor value oscillates within the scenario's range
    - Rate correlates with value level
    - Battery drains at the configured rate
    - GPS coordinates drift along a simulated path
    - Temperature rises toward a steady state
    """

    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.t0 = time.time()
        self.frame_num = 0

        # Initial state
        value_range = scenario.get("value_range", [30, 70])
        self.value = (value_range[0] + value_range[1]) / 2
        self.rate = scenario.get("rate_range", [100, 500])[0]
        self.battery_pct = 85.0
        self.counter = 0
        self.temperature = 25.0
        self.gps_lat = 0.0
        self.gps_lon = 0.0
        self.heading = 0.0

    def next_frame(self) -> dict:
        """Generate the next telemetry frame."""
        dt = 0.1  # ~100ms per frame
        elapsed = time.time() - self.t0
        self.frame_num += 1

        # Value: sinusoidal oscillation within range
        value_range = self.scenario.get("value_range", [30, 70])
        value_mid = (value_range[0] + value_range[1]) / 2
        value_amp = (value_range[1] - value_range[0]) / 2
        self.value = value_mid + value_amp * math.sin(elapsed * 0.3)
        self.value += random.gauss(0, 0.5)  # Jitter
        self.value = max(value_range[0], min(value_range[1], self.value))

        # Rate: correlates with value, with random variation
        rate_range = self.scenario.get("rate_range", [100, 500])
        if value_range[1] > value_range[0]:
            rate_ratio = (self.value - value_range[0]) / (
                value_range[1] - value_range[0]
            )
        else:
            rate_ratio = 0.5
        self.rate = rate_range[0] + rate_ratio * (rate_range[1] - rate_range[0])
        self.rate += random.gauss(0, 5)
        self.rate = max(0, self.rate)

        # Battery: drain (or recover) over time
        drain_rate = self.scenario.get("drain_pct_per_s", 0.015)
        self.battery_pct -= drain_rate * dt
        self.battery_pct = max(0, min(100, self.battery_pct))

        # Counter: accumulate based on value
        self.counter += self.value * dt

        # Temperature: exponential rise toward steady state
        target_temp = 45.0 if self.value > 0 else 25.0
        self.temperature += (target_temp - self.temperature) * 0.01

        # GPS: drift along heading
        speed_ms = self.value * 0.1
        self.gps_lat += (
            math.cos(math.radians(self.heading))
            * speed_ms * dt / 111320
        )
        self.gps_lon += (
            math.sin(math.radians(self.heading))
            * speed_ms * dt
            / max(111320 * math.cos(math.radians(self.gps_lat)), 1)
        )
        # Gentle heading wander
        self.heading += random.gauss(0, 0.2)
        self.heading %= 360

        # State selection based on value
        if self.value < 1:
            state = "idle"
        elif self.value < 30:
            state = "low"
        elif self.value < 60:
            state = "normal"
        else:
            state = "high"

        # Voltage: correlates with battery
        voltage = 3.0 + (self.battery_pct / 100) * 1.2

        return {
            "type": "telemetry",
            "frame": self.frame_num,
            "timestamp": time.time(),
            "sensor_value": round(self.value, 1),
            "rate": round(self.rate),
            "state": state,
            "battery_pct": round(self.battery_pct, 1),
            "voltage": round(voltage, 2),
            "counter": round(self.counter, 1),
            "temperature_c": round(self.temperature, 1),
            "gps": {
                "lat": round(self.gps_lat, 6),
                "lon": round(self.gps_lon, 6),
                "heading": round(self.heading, 1),
                "altitude_m": 0,
            },
        }


# Default scenario definitions (used if not in config)
DEFAULT_SCENARIOS = {
    "idle": {
        "value_range": [0, 0],
        "rate_range": [0, 0],
        "drain_pct_per_s": 0.001,
    },
    "normal": {
        "value_range": [30, 70],
        "rate_range": [100, 500],
        "drain_pct_per_s": 0.015,
    },
    "variable": {
        "value_range": [0, 55],
        "rate_range": [50, 1000],
        "drain_pct_per_s": 0.02,
    },
    "recovery": {
        "value_range": [5, 40],
        "rate_range": [0, 200],
        "drain_pct_per_s": -0.03,
    },
    "peak": {
        "value_range": [0, 100],
        "rate_range": [500, 2000],
        "drain_pct_per_s": 0.05,
    },
}
