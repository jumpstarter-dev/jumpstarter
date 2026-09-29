package exporterset

import (
	"context"
	"errors"
	"strings"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset/provisioners/cuttlefish"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

func TestCuttlefishNetworkPolicyReconciliation(t *testing.T) {
	scheme := newScheme(t)
	if err := networkingv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	es := makeExporterSet()
	c := fake.NewClientBuilder().WithScheme(scheme).Build()
	r := &ExporterSetReconciler{Client: c, Scheme: scheme, Provisioner: cuttlefish.New("dev")}
	ctx := context.Background()
	if err := r.syncNetworkPolicy(ctx, es); err != nil {
		t.Fatal(err)
	}
	policy := &networkingv1.NetworkPolicy{}
	key := client.ObjectKey{Namespace: es.Namespace, Name: "cuttlefish-" + string(es.UID)}
	if err := c.Get(ctx, key, policy); err != nil {
		t.Fatal(err)
	}
	if !metav1.IsControlledBy(policy, es) || len(policy.Spec.Ingress) != 0 {
		t.Fatalf("unowned or permissive policy: %#v", policy)
	}
	policy.Spec.Ingress = []networkingv1.NetworkPolicyIngressRule{{}}
	if err := c.Update(ctx, policy); err != nil {
		t.Fatal(err)
	}
	if err := r.syncNetworkPolicy(ctx, es); err != nil {
		t.Fatal(err)
	}
	if err := c.Get(ctx, key, policy); err != nil {
		t.Fatal(err)
	}
	if len(policy.Spec.Ingress) != 0 {
		t.Fatal("policy drift not corrected")
	}
	if err := c.Delete(ctx, policy); err != nil {
		t.Fatal(err)
	}
	if err := r.syncNetworkPolicy(ctx, es); err != nil {
		t.Fatal(err)
	}
	if err := c.Get(ctx, key, policy); err != nil {
		t.Fatal("deleted policy not recreated", err)
	}
}

func TestPolicyFailurePreventsWorkloadCreation(t *testing.T) {
	scheme := newScheme(t)
	if err := networkingv1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	es := makeExporterSet()
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: es.Spec.VirtualTargetClassName, Namespace: es.Namespace},
		Spec:       virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: cuttlefish.ProvisionerName},
	}
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(es, vtc).WithInterceptorFuncs(interceptor.Funcs{
		Create: func(ctx context.Context, c client.WithWatch, obj client.Object, opts ...client.CreateOption) error {
			if _, ok := obj.(*networkingv1.NetworkPolicy); ok {
				return errors.New("network policy denied")
			}
			return c.Create(ctx, obj, opts...)
		},
	}).Build()
	r := &ExporterSetReconciler{Client: c, Scheme: scheme, Provisioner: cuttlefish.New("dev")}
	_, err := r.Reconcile(context.Background(), ctrl.Request{NamespacedName: client.ObjectKeyFromObject(es)})
	if err == nil || !strings.Contains(err.Error(), "network policy denied") {
		t.Fatalf("expected policy error: %v", err)
	}
	var pods corev1.PodList
	if err := c.List(context.Background(), &pods); err != nil {
		t.Fatal(err)
	}
	if len(pods.Items) != 0 {
		t.Fatal("created workload without network isolation")
	}
}
