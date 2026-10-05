import click
from jumpstarter_driver_video.client import VideoClient

from .common import UStreamerState


class UStreamerClient(VideoClient):
    """UStreamer client class

    Client methods for the UStreamer driver. Inherits snapshot and
    streaming functionality from VideoClient.
    """

    def state(self):
        """Get state of ustreamer service"""
        return UStreamerState.model_validate(self.call("state"))

    def cli(self):
        video = super().cli()

        @video.command()
        def state():
            """Show video source state"""
            s = self.state()
            src = s.result.source
            enc = s.result.encoder
            click.echo(f"Online:     {src.online}")
            click.echo(f"Resolution: {src.resolution.width}x{src.resolution.height}")
            click.echo(f"FPS:        {src.captured_fps}/{src.desired_fps}")
            click.echo(f"Encoder:    {enc.type} (quality: {enc.quality})")

        return video
