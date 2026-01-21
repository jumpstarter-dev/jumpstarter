package authorization

import (
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/util/validation"
)

func TestNormalizeName(t *testing.T) {
	testcases := []struct {
		input  string
		output string
	}{
		{
			input:  "foo",
			output: "oidc-foo-2c26b4",
		},
		{
			input:  "foo@example.com",
			output: "oidc-foo-example-com-321ba1",
		},
		{
			input:  "foo@@@@@example.com",
			output: "oidc-foo-example-com-5ac340",
		},
		{
			input:  "@foo@example.com@",
			output: "oidc-foo-example-com-5be6ea",
		},
		{
			input:  strings.Repeat("foo", 30),
			output: "oidc-foofoofoofoofoofoofoofoofoofoofoofoof-4ac4a7",
		},
	}
	for _, testcase := range testcases {
		result := normalizeName(testcase.input)
		if validation.IsDNS1123Subdomain(result) != nil {
			t.Errorf("normalizing the name %s does not produce a valid RFC1123 subdomain, but %s",
				testcase.input, result)
		}
		if result != testcase.output {
			t.Errorf("normalizing the name %s does not produce the expected output %s, but %s",
				testcase.input, testcase.output, result)
		}
	}
}
