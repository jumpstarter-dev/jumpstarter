/*
Copyright 2026. The Jumpstarter Authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package e2e

import (
	"fmt"
	"path/filepath"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

// pythonBin returns the interpreter to run inside `jmp shell`. Commands launched
// after `jmp shell -- ...` do NOT inherit the venv on $PATH, so we resolve the
// absolute venv python when PYTHON_VENV is set and fall back to bare python3.
func pythonBin() string {
	if venv := PythonVenv(); venv != "" {
		return filepath.Join(venv, "bin", "python")
	}
	return "python3"
}

// resourceRoundTripScript drives the resource byte plane end to end through the
// *native* per-interface gRPC transport: `write_local_file` opens a client-side
// resource (a local file), hands the driver the resource handle (serialized as a
// JSON string over the native wire — the exact crossing the handle-encoding fix
// corrected), and the driver streams the bytes from the client into its storage;
// `read_local_file` streams them back. Asserting byte equality proves both
// directions of the byte plane survive the native path. Self-contained: it makes
// its own temp files and prints a sentinel the Go test greps for.
const resourceRoundTripScript = `
import os, tempfile
from jumpstarter.common.utils import env

data = os.urandom(4 << 20)  # 4 MiB incompressible -> exercises multiple byte-plane chunks
d = tempfile.mkdtemp(prefix="jmp-e2e-resource-")
src = os.path.join(d, "src.bin")
dst = os.path.join(d, "dst.bin")
with open(src, "wb") as f:
    f.write(data)

with env() as client:
    client.storage.write_local_file(src)  # client -> exporter (resource read)
    client.storage.read_local_file(dst)    # exporter -> client (resource send)

with open(dst, "rb") as f:
    got = f.read()
assert got == data, "resource round-trip mismatch: %d != %d bytes" % (len(got), len(data))
print("RESOURCE_ROUNDTRIP_OK")
`

var _ = Describe("Native Resource Transfer E2E Tests", Label("direct-listener"), Ordered, func() {
	var (
		tracker      *ProcessTracker
		listenerPort = 19092
		exporterDir  string
	)

	BeforeAll(func() {
		tracker = NewProcessTracker()
		exporterDir = filepath.Join(RepoRoot(), "e2e", "exporters")
	})

	AfterEach(func() {
		if CurrentSpecReport().Failed() {
			tracker.DumpLogs(250)
		}
		tracker.StopAll()

		// Wait for the listener port to be fully released before the next test.
		Eventually(func() error {
			_, err := RunCmd("nc", "-z", "127.0.0.1", fmt.Sprintf("%d", listenerPort))
			return err
		}, 10*time.Second, 500*time.Millisecond).Should(HaveOccurred(),
			"port %d should be closed after stopping exporter", listenerPort)
	})

	AfterAll(func() {
		tracker.Cleanup()
	})

	It("round-trips a file through the resource byte plane over the native transport", func() {
		config := filepath.Join(exporterDir, "exporter-direct-storage.yaml")
		tracker.StartDirectExporter(config, listenerPort, "", false)
		WaitForDirectExporterReady(listenerPort, "")

		out, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--", pythonBin(), "-c", resourceRoundTripScript)
		Expect(err).NotTo(HaveOccurred(), out)
		Expect(out).To(ContainSubstring("RESOURCE_ROUNDTRIP_OK"))
	})
})
