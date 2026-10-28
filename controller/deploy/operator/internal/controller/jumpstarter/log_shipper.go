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

package jumpstarter

import (
	"context"
	"fmt"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

const (
	shipperTypeNone     = "none"
	shipperTypePromtail = "promtail"
	shipperTypeAlloy    = "alloy"
	shipperTypeVector   = "vector"
)

// shipperType returns the configured shipper type, defaulting to "none".
func shipperType(js *operatorv1alpha1.Jumpstarter) string {
	if js.Spec.Telemetry == nil {
		return shipperTypeNone
	}
	switch js.Spec.Telemetry.Logging.Shipper.Type {
	case shipperTypePromtail, shipperTypeAlloy, shipperTypeVector:
		return js.Spec.Telemetry.Logging.Shipper.Type
	default:
		return shipperTypeNone
	}
}

// logShipperConfigMapName returns the shipper ConfigMap name.
func logShipperConfigMapName(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf("%s-log-shipper", js.Name)
}

// logShipperLabels returns the labels for the shipper ConfigMap.
func logShipperLabels(js *operatorv1alpha1.Jumpstarter, shipper string) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":      "jumpstarter",
		"app.kubernetes.io/instance":  js.Name,
		"app.kubernetes.io/component": "logging",
		"jumpstarter.dev/log-shipper": shipper,
	}
}

// buildLogShipperConfigMap returns the desired shipper ConfigMap, or nil when
// shipper generation is disabled.
func buildLogShipperConfigMap(js *operatorv1alpha1.Jumpstarter) *corev1.ConfigMap {
	shipper := shipperType(js)
	if shipper == shipperTypeNone {
		return nil
	}
	key, content := renderShipper(js, shipper)
	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      logShipperConfigMapName(js),
			Namespace: js.Namespace,
			Labels:    logShipperLabels(js, shipper),
		},
		Data: map[string]string{
			key:         content,
			"README.md": renderShipperReadme(js, shipper, key),
		},
	}
}

// reconcileLogShipper creates, updates, or removes the shipper ConfigMap.
func (r *JumpstarterReconciler) reconcileLogShipper(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	desired := buildLogShipperConfigMap(js)
	if desired == nil {
		return r.deleteObjectIfExists(ctx, types.NamespacedName{
			Name: logShipperConfigMapName(js), Namespace: js.Namespace,
		}, &corev1.ConfigMap{})
	}

	existing := &corev1.ConfigMap{}
	err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, existing)
	if apierrors.IsNotFound(err) {
		if err := controllerutil.SetControllerReference(js, desired, r.Scheme); err != nil {
			return err
		}
		if err := r.Create(ctx, desired); err != nil {
			return fmt.Errorf("create log shipper configmap %s: %w", desired.Name, err)
		}
		log.Info("Created log shipper ConfigMap", "name", desired.Name, "shipper", shipperType(js))
		return nil
	}
	if err != nil {
		return err
	}
	if !configMapNeedsUpdate(existing, desired) {
		return nil
	}
	existing.Labels = desired.Labels
	existing.Data = desired.Data
	if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
		return err
	}
	if err := r.Update(ctx, existing); err != nil {
		return fmt.Errorf("update log shipper configmap %s: %w", desired.Name, err)
	}
	log.Info("Updated log shipper ConfigMap", "name", desired.Name, "shipper", shipperType(js))
	return nil
}

// logShipperCondition reports the LogShipperReady condition state.
func (r *JumpstarterReconciler) logShipperCondition(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string, string) {
	shipper := shipperType(js)
	if shipper == shipperTypeNone {
		return false, "Disabled", "Log shipper integration is disabled (shipper type none)"
	}
	cm := &corev1.ConfigMap{}
	if err := r.Get(ctx, types.NamespacedName{Name: logShipperConfigMapName(js), Namespace: js.Namespace}, cm); err != nil {
		return false, "Failed", fmt.Sprintf("log shipper ConfigMap %s is not available: %v", logShipperConfigMapName(js), err)
	}
	return true, "Created", fmt.Sprintf("Log shipper configuration generated for %s", shipper)
}
