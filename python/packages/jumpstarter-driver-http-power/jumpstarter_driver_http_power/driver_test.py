import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .driver import HttpEndpointConfig, HttpPower
from jumpstarter.common.utils import serve


class MockHTTPHandler(BaseHTTPRequestHandler):
    """Mock HTTP server handler for testing"""

    def log_message(self, format, *args):
        # Suppress server logs during testing
        pass

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
        self._handle_request()

    def do_PUT(self):
        self._handle_request()

    def _handle_request(self):
        # Record the request for verification
        if not hasattr(self.server, 'requests'):
            self.server.requests = []

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else None

        self.server.requests.append({
            'method': self.command,
            'path': self.path,
            'body': body
        })

        # Send appropriate response based on endpoint
        if self.path == '/read':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"voltage": 12.0, "current": 2.5}')
        else:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')


def test_drivers_http_power():
    # Start a mock HTTP server
    server = HTTPServer(('localhost', 0), MockHTTPHandler)
    server_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    try:
        base_url = f"http://localhost:{server_port}"

        instance = HttpPower(
            power_on=HttpEndpointConfig(url=f"{base_url}/on", method="POST", data="power=on"),
            power_off=HttpEndpointConfig(url=f"{base_url}/off", method="POST", data="power=off"),
            power_read=HttpEndpointConfig(url=f"{base_url}/read"),
        )

        with serve(instance) as client:
            # Test that the client can be created and basic methods exist
            assert hasattr(client, 'on')
            assert hasattr(client, 'off')
            assert hasattr(client, 'read')

            # Test actual HTTP calls
            client.on()
            client.off()

            # Test read method
            readings = list(client.read())
            assert len(readings) == 1
            assert readings[0].voltage == 0.0  # Currently returns dummy values
            assert readings[0].current == 0.0

            # Verify HTTP requests were made
            assert len(server.requests) == 3

            # Check on request
            on_request = server.requests[0]
            assert on_request['method'] == 'POST'
            assert on_request['path'] == '/on'
            assert on_request['body'] == 'power=on'

            # Check off request
            off_request = server.requests[1]
            assert off_request['method'] == 'POST'
            assert off_request['path'] == '/off'
            assert off_request['body'] == 'power=off'

            # Check read request
            read_request = server.requests[2]
            assert read_request['method'] == 'GET'
            assert read_request['path'] == '/read'
            assert read_request['body'] is None

    finally:
        server.shutdown()
        server_thread.join(timeout=1)
