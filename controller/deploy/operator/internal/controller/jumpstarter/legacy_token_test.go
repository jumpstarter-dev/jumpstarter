package jumpstarter

import (
	"context"
	"reflect"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/yaml"
)

func TestStoredTokenFallbackConfig(t *testing.T) {
	for _, allowed := range []*bool{nil, ptr.To(false), ptr.To(true)} {
		js := &operatorv1alpha1.Jumpstarter{}
		js.Spec.Authentication.Internal.Enabled = true
		js.Spec.Authentication.Internal.AllowStoredTokenFallback = allowed
		cfg, err := (&JumpstarterReconciler{}).buildConfig(context.Background(), js)
		if err != nil {
			t.Fatal(err)
		}
		data, err := yaml.Marshal(cfg)
		if err != nil {
			t.Fatal(err)
		}
		var parsed config.Config
		if err := yaml.UnmarshalStrict(data, &parsed); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(parsed.Authentication.Internal.AllowStoredTokenFallback, allowed) {
			t.Fatal("operator lost the tri-state stored token fallback setting")
		}
	}
}
