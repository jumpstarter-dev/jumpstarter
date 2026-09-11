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

package service

import "testing"

func TestMetricsHTTPEnabled(t *testing.T) {
	t.Parallel()
	cases := []struct {
		addr string
		want bool
	}{
		{addr: "", want: false},
		{addr: "0", want: false},
		{addr: ":8080", want: true},
	}
	for _, tc := range cases {
		if got := metricsHTTPEnabled(tc.addr); got != tc.want {
			t.Errorf("metricsHTTPEnabled(%q) = %v, want %v", tc.addr, got, tc.want)
		}
	}
}
