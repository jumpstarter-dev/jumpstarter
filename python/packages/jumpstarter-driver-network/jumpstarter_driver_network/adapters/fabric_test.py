from socketserver import BaseRequestHandler, TCPServer
from threading import Thread
from time import sleep

from paramiko import AUTH_SUCCESSFUL, OPEN_SUCCEEDED, ServerInterface, Transport  # ty: ignore[unresolved-import]
from paramiko.rsakey import RSAKey

from ..driver import TcpNetwork
from .fabric import FabricAdapter
from jumpstarter.common.utils import serve


class SSHServer(ServerInterface):
    def check_auth_password(self, username, password):
        return AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        return OPEN_SUCCEEDED

    def check_channel_exec_request(self, channel, command):
        channel.sendall("dummy output")
        channel.send_exit_status(0)
        channel.shutdown_write()
        return True


class SSHHandler(BaseRequestHandler):
    def setup(self):
        self.transport = Transport(self.request)
        self.transport.add_server_key(RSAKey.generate(1024))
        self.transport.start_server(server=SSHServer())

    def handle(self):
        while self.transport.is_active():
            sleep(1)

    def finish(self):
        self.transport.close()


def test_client_adapter_fabric():
    server = TCPServer(("127.0.0.1", 0), SSHHandler)
    server_thread = Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    with serve(TcpNetwork(host=server.server_address[0], port=server.server_address[1])) as client:
        with FabricAdapter(client=client, connect_kwargs={"password": "password"}) as conn:
            conn.run("dummy command")

    server.shutdown()
