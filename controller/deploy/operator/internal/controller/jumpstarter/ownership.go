/*
Copyright 2026.
*/

package jumpstarter

import (
	"fmt"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// ensureControlledBy prevents the operator from adopting or deleting an object
// that merely shares a desired name or label with an observability resource.
func ensureControlledBy(js *operatorv1alpha1.Jumpstarter, obj metav1.Object) error {
	if !metav1.IsControlledBy(obj, js) {
		return fmt.Errorf("%q is not controlled by Jumpstarter %q", obj.GetName(), js.Name)
	}
	return nil
}
