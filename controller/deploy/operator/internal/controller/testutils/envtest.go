/*
Copyright 2025.

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

package testutils

import (
	"os"
	"path/filepath"

	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

// GetFirstFoundEnvTestBinaryDir locates the first binary in the specified path.
// ENVTEST-based tests depend on specific binaries, usually located in paths set by
// controller-runtime. When running tests directly (e.g., via an IDE) without using
// Makefile targets, the 'BinaryAssetsDirectory' must be explicitly configured.
//
// This function streamlines the process by finding the required binaries, similar to
// setting the 'KUBEBUILDER_ASSETS' environment variable. To ensure the binaries are
// properly set up, run 'make setup-envtest' beforehand.
//
// The depth parameter specifies how many directories up to traverse from the test file
// to reach the operator root (where bin/k8s is located).
func GetFirstFoundEnvTestBinaryDir(depth int) string {
	// Build the path based on depth
	pathComponents := make([]string, 0, depth+2)
	for i := 0; i < depth; i++ {
		pathComponents = append(pathComponents, "..")
	}
	pathComponents = append(pathComponents, "bin", "k8s")

	basePath := filepath.Join(pathComponents...)
	entries, err := os.ReadDir(basePath)
	if err != nil {
		logf.Log.Error(err, "Failed to read directory", "path", basePath)
		return ""
	}
	for _, entry := range entries {
		if entry.IsDir() {
			return filepath.Join(basePath, entry.Name())
		}
	}
	return ""
}
