/*
Copyright 2026 The Jumpstarter Authors

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

package kubevirt

import (
	"context"
	"fmt"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/rest"
	kubevirtv1 "kubevirt.io/api/core/v1"
	cdiv1beta1 "kubevirt.io/containerized-data-importer-api/pkg/apis/core/v1beta1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

func TestInitialDisksWithoutVMFinalizerPermission(t *testing.T) {
	// Exercise Kubernetes admission and RBAC, which the fake client does not run.
	// No KubeVirt/CDI controllers are needed to validate owner reference admission.
	testEnv := &envtest.Environment{
		BinaryAssetsDirectory: filepath.Join("..", "..", "..", "..", "bin", "k8s",
			fmt.Sprintf("1.30.0-%s-%s", runtime.GOOS, runtime.GOARCH)),
		CRDs: []*apiextensionsv1.CustomResourceDefinition{
			storageTestCRD(kubevirtv1.VirtualMachineGroupVersionKind, "virtualmachines"),
			storageTestCRD(cdiv1beta1.SchemeGroupVersion.WithKind("DataVolume"), "datavolumes"),
		},
	}
	testEnv.ControlPlane.GetAPIServer().Configure().
		Set("enable-admission-plugins", "OwnerReferencesPermissionEnforcement")
	config, err := testEnv.Start()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := testEnv.Stop(); err != nil {
			t.Error(err)
		}
	})
	scheme := remoteScheme()
	if err := rbacv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	admin, err := client.New(config, client.Options{Scheme: scheme})
	if err != nil {
		t.Fatal(err)
	}
	params, err := parseParams(namedDiskParams())
	if err != nil {
		t.Fatal(err)
	}
	vm, err := New("dev", nil).buildVirtualMachine(testExporter, params)
	if err != nil {
		t.Fatal(err)
	}
	const user = "disk-provisioner"
	ctx := context.Background()
	for _, obj := range []client.Object{
		&corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: vm.Namespace}},
		vm,
		&rbacv1.Role{
			ObjectMeta: metav1.ObjectMeta{Name: user, Namespace: vm.Namespace},
			Rules: []rbacv1.PolicyRule{{
				APIGroups: []string{cdiv1beta1.SchemeGroupVersion.Group},
				Resources: []string{"datavolumes"}, Verbs: []string{"create", "get", "delete"},
			}},
		},
		&rbacv1.RoleBinding{
			ObjectMeta: metav1.ObjectMeta{Name: user, Namespace: vm.Namespace},
			RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "Role", Name: user},
			Subjects:   []rbacv1.Subject{{APIGroup: rbacv1.GroupName, Kind: "User", Name: user}},
		},
	} {
		if err := admin.Create(ctx, obj); err != nil {
			t.Fatal(err)
		}
	}
	limitedConfig := rest.CopyConfig(config)
	limitedConfig.Impersonate = rest.ImpersonationConfig{UserName: user}
	remote, err := client.New(limitedConfig, client.Options{Scheme: scheme})
	if err != nil {
		t.Fatal(err)
	}
	// Wait for the role binding to propagate, proving ordinary DV creation works.
	probe := initialDataVolume(vm, "permission-probe", params.Storage.Disks["root"])
	probe.OwnerReferences = nil
	if err := wait.PollUntilContextTimeout(ctx, 100*time.Millisecond, 10*time.Second, true,
		func(ctx context.Context) (bool, error) {
			err := remote.Create(ctx, probe.DeepCopy())
			if apierrors.IsForbidden(err) {
				return false, nil
			}
			return err == nil, err
		}); err != nil {
		t.Fatalf("DataVolume creation permission did not become available: %v", err)
	}
	// Negative control: blocking VM deletion must still require extra permission.
	blocked := initialDataVolume(vm, "blocked-probe", params.Storage.Disks["root"])
	blocked.OwnerReferences[0].BlockOwnerDeletion = new(true)
	if err := remote.Create(ctx, blocked); !apierrors.IsForbidden(err) || !strings.Contains(err.Error(), "blockOwnerDeletion") {
		t.Fatalf("expected owner-deletion permission rejection, got %v", err)
	}
	for range 2 {
		if err := ensureDisks(ctx, remote, vm, params); err != nil {
			t.Fatalf("initial disk provisioning must work without VM finalizer permission: %v", err)
		}
	}
	for _, name := range []string{"root", "data"} {
		var dv cdiv1beta1.DataVolume
		if err := remote.Get(ctx, client.ObjectKey{Namespace: vm.Namespace, Name: vm.Name + "-" + name}, &dv); err != nil {
			t.Fatal(err)
		}
		if !metav1.IsControlledBy(&dv, vm) {
			t.Fatalf("disk %s lost its VM ownership", name)
		}
	}
}

// These minimal schemas exercise generic API admission, not KubeVirt/CDI validation.
func storageTestCRD(gvk schema.GroupVersionKind, plural string) *apiextensionsv1.CustomResourceDefinition {
	return &apiextensionsv1.CustomResourceDefinition{
		ObjectMeta: metav1.ObjectMeta{Name: plural + "." + gvk.Group},
		Spec: apiextensionsv1.CustomResourceDefinitionSpec{
			Group: gvk.Group, Scope: apiextensionsv1.NamespaceScoped,
			Names: apiextensionsv1.CustomResourceDefinitionNames{Kind: gvk.Kind, Plural: plural},
			Versions: []apiextensionsv1.CustomResourceDefinitionVersion{{
				Name: gvk.Version, Served: true, Storage: true,
				Schema: &apiextensionsv1.CustomResourceValidation{
					OpenAPIV3Schema: &apiextensionsv1.JSONSchemaProps{Type: "object", XPreserveUnknownFields: new(true)},
				},
			}},
		},
	}
}
