/*
Copyright 2026.

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

import "testing"

func TestIsNoLeasesFound(t *testing.T) {
	tests := []struct {
		name   string
		output string
		want   bool
	}{
		{
			name:   "exact cli message",
			output: "no leases found matching the criteria",
			want:   true,
		},
		{
			name:   "message embedded in output",
			output: "Error: no leases found matching the criteria\n",
			want:   true,
		},
		{
			name:   "different error",
			output: "Error: connection refused",
			want:   false,
		},
		{
			name:   "empty output",
			output: "",
			want:   false,
		},
		{
			name:   "get leases empty result is not a delete",
			output: "No resources found.",
			want:   false,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsNoLeasesFound(tc.output); got != tc.want {
				t.Errorf("IsNoLeasesFound(%q) = %v, want %v", tc.output, got, tc.want)
			}
		})
	}
}
