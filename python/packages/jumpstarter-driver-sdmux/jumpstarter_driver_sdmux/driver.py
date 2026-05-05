from pathlib import Path

class UsbSdMux:
    sg_device: str | None = None

    @staticmethod
    def search_sg_device():
        sg_paths = list(Path("/dev").glob("sg*"))

        if len(sg_paths) == 1:
            UsbSdMux.sg_device = str(sg_paths[0])
            return UsbSdMux.sg_device
        else:
            return None
