from pydantic import BaseModel

ESC = "\x1b"


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
        except Exception:
            return "24"
