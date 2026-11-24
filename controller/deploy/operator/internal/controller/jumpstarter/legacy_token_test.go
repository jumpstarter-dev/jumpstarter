package jumpstarter

import (
	"context"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"sigs.k8s.io/yaml"
)

func TestLegacyTokenDisableConfig(t *testing.T) {
	for _, disabled := range []bool{false, true} {
		js := &operatorv1alpha1.Jumpstarter{}
		js.Spec.Authentication.Internal.Enabled = true
		js.Spec.Authentication.Internal.DisableLegacyTokenAuthentication = disabled
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
		if parsed.Authentication.Internal.DisableLegacyTokenAuthentication != disabled {
			t.Fatal("operator lost the migration disable switch")
		}
	}
}
