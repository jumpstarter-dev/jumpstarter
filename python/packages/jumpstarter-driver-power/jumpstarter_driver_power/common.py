from pydantic import BaseModel


class PowerReading(BaseModel):
    voltage: float
    current: float

    @property
    def apparent_power(self):
        return self.voltage * self.current
