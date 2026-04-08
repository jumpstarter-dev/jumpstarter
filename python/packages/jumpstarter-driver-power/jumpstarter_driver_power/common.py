from pydantic import BaseModel


class PowerReading(BaseModel):
    """Real-time power measurement from the DUT power rail."""

    voltage: float
    """Measured rail voltage in volts."""

    current: float
    """Measured rail current in amperes."""

    @property
    def apparent_power(self):
        return self.voltage * self.current
