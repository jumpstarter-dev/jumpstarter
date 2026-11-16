from pydantic import BaseModel

ESC = "\x1b"

# How long reboot_to_console waits for the prompt after a power cycle when the
# exporter does not configure prompt_timeout
DEFAULT_PROMPT_TIMEOUT = 10.0


class DhcpInfo(BaseModel):
    ip_address: str
    gateway: str
    netmask: str

    @property
    def cidr(self) -> str:
        try:
            octets = [int(x) for x in self.netmask.split(".")]
            binary = "".join([bin(x)[2:].zfill(8) for x in octets])
            return str(binary.count("1"))
        except Exception:  # pragma: no cover  # noqa: BLE001
            return "24"
