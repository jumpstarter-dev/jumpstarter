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
	"errors"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	kubevirtv1 "kubevirt.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

func TestCleanupRemoteResources(t *testing.T) {
	for _, tt := range []struct {
		name       string
		policy     virtualtargetv1alpha1.ReclaimPolicy
		failDelete bool
	}{
		{"delete", virtualtargetv1alpha1.ReclaimPolicyDelete, false},
		{"retain", virtualtargetv1alpha1.ReclaimPolicyRetain, false},
		{"retry partial cleanup", virtualtargetv1alpha1.ReclaimPolicyDelete, true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			ctx := context.Background()
			vm := &kubevirtv1.VirtualMachine{ObjectMeta: metav1.ObjectMeta{Name: testExporter.Name, Namespace: "remote-ns"}}
			ssh := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: vm.Name + "-ssh", Namespace: vm.Namespace}}
			web := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: vm.Name + "-web", Namespace: vm.Namespace}}
			objects := []client.Object{vm, ssh, web}
			cleanupErr := errors.New("remote API unavailable")
			failDelete := tt.failDelete
			remote := fake.NewClientBuilder().WithScheme(remoteScheme()).WithObjects(objects...).
				WithInterceptorFuncs(interceptor.Funcs{
					Delete: func(ctx context.Context, c client.WithWatch, obj client.Object, opts ...client.DeleteOption) error {
						if failDelete && obj.GetName() == web.Name {
							failDelete = false
							return cleanupErr
						}
						return c.Delete(ctx, obj, opts...)
					},
				}).Build()
			prov, es := cleanupTestProvisioner(t, tt.policy, remote)
			if tt.failDelete {
				if err := prov.Cleanup(ctx, es, testExporter); !errors.Is(err, cleanupErr) {
					t.Fatalf("cleanup must report the remote failure: %v", err)
				}
				if err := remote.Get(ctx, client.ObjectKeyFromObject(vm), vm); !apierrors.IsNotFound(err) {
					t.Fatalf("test must reach a partial cleanup after VM deletion: %v", err)
				}
				if err := remote.Get(ctx, client.ObjectKeyFromObject(web), web); err != nil {
					t.Fatalf("failed service deletion must remain retryable: %v", err)
				}
			}
			for range 2 {
				if err := prov.Cleanup(ctx, es, testExporter); err != nil {
					t.Fatalf("cleanup must be retryable and idempotent: %v", err)
				}
			}
			for _, obj := range objects {
				err := remote.Get(ctx, client.ObjectKeyFromObject(obj), obj)
				if tt.policy == virtualtargetv1alpha1.ReclaimPolicyRetain {
					if err != nil {
						t.Errorf("retained %s was lost: %v", obj.GetName(), err)
					}
				} else if !apierrors.IsNotFound(err) {
					t.Errorf("%s survived cleanup: %v", obj.GetName(), err)
				}
			}
		})
	}
}

func cleanupTestProvisioner(
	t *testing.T,
	policy virtualtargetv1alpha1.ReclaimPolicy,
	remote client.Client,
) (*Provisioner, *virtualtargetv1alpha1.ExporterSet) {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := virtualtargetv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: "kubevirt-class", Namespace: testExporter.Namespace},
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner:          ProvisionerName,
			ReclaimPolicy:        policy,
			CredentialsSecretRef: &corev1.LocalObjectReference{Name: "credentials"},
			Parameters: &apiextensionsv1.JSON{Raw: []byte(`{
				"namespace":"remote-ns", "image":"quay.io/test/image:latest",
				"sshAccess":"NodePort", "forwardPorts":{"web":8080}
			}`)},
		},
	}
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "credentials", Namespace: vtc.Namespace}}
	management := fake.NewClientBuilder().WithScheme(scheme).WithObjects(vtc, secret).Build()
	if err := management.Get(context.Background(), client.ObjectKeyFromObject(secret), secret); err != nil {
		t.Fatal(err)
	}
	prov := New("dev", management)
	prov.remoteClients.set(vtc.Namespace+"/"+vtc.Name+"/"+secret.ResourceVersion, remote)
	es := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Namespace: vtc.Namespace},
		Spec:       virtualtargetv1alpha1.ExporterSetSpec{VirtualTargetClassName: vtc.Name},
	}
	return prov, es
}
