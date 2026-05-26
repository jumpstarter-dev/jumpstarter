# Get the PEM certificate contents
pem = proxy.get_ca_cert()

# Write to a local file
from pathlib import Path

Path("/tmp/mitmproxy-ca.pem").write_text(pem)

# Or push directly to the DUT via serial/ssh/adb
dut.write_file("/etc/ssl/certs/mitmproxy-ca.pem", pem)
