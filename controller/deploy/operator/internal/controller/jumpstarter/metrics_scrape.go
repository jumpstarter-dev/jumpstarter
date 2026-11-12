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
	"errors"
	"fmt"
	"maps"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

const (
	// metricsPort is the /metrics port shared by controller and router. The port
	// name (metricsPortName) is declared alongside the telemetry constants.
	metricsPort = int32(8080)

	// metricsServiceLabel marks Services that a ServiceMonitor selects.
	metricsServiceLabel = "jumpstarter.dev/metrics"

	// logScrapeLabel marks pods whose stdout logs a shipper should collect.
	logScrapeLabel = "jumpstarter.dev/log-scrape"
	// logFormatLabel declares the stdout log format for shippers.
	logFormatLabel = "jumpstarter.dev/log-format"

	serviceMonitorGroup   = "monitoring.coreos.com"
	serviceMonitorVersion = "v1"
	serviceMonitorKind    = "ServiceMonitor"
)

// serviceMonitorEnabled reports whether Prometheus autodiscovery is enabled.
// An unset field means enabled (CRD default true).
func serviceMonitorEnabled(js *operatorv1alpha1.Jumpstarter) bool {
	if js.Spec.Telemetry == nil || js.Spec.Telemetry.Metrics.ServiceMonitor == nil {
		return true
	}
	return *js.Spec.Telemetry.Metrics.ServiceMonitor
}

// metricsServiceName returns the metrics Service name for a component.
func metricsServiceName(js *operatorv1alpha1.Jumpstarter, component string) string {
	return fmt.Sprintf("%s-%s-metrics", js.Name, component)
}

// metricsServiceLabels returns the labels shared by metrics Services and their
// ServiceMonitors.
func metricsServiceLabels(js *operatorv1alpha1.Jumpstarter, component string) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":      "jumpstarter",
		"app.kubernetes.io/instance":  js.Name,
		"app.kubernetes.io/component": component,
		metricsServiceLabel:           "true",
	}
}

// desiredMetricsServices returns the metrics-only Services for controller and
// router. Telemetry already exposes a metrics port on its existing Service.
func desiredMetricsServices(js *operatorv1alpha1.Jumpstarter) []*corev1.Service {
	return []*corev1.Service{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      metricsServiceName(js, "controller"),
				Namespace: js.Namespace,
				Labels:    metricsServiceLabels(js, "controller"),
			},
			Spec: corev1.ServiceSpec{
				Type: corev1.ServiceTypeClusterIP,
				Selector: map[string]string{
					"component":  "controller",
					"controller": js.Name,
				},
				Ports: []corev1.ServicePort{{
					Name:       metricsPortName,
					Port:       metricsPort,
					TargetPort: intstr.FromString(metricsPortName),
					Protocol:   corev1.ProtocolTCP,
				}},
			},
		},
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      metricsServiceName(js, "router"),
				Namespace: js.Namespace,
				Labels:    metricsServiceLabels(js, "router"),
			},
			Spec: corev1.ServiceSpec{
				Type: corev1.ServiceTypeClusterIP,
				Selector: map[string]string{
					"component": "router",
					"router":    js.Name,
				},
				Ports: []corev1.ServicePort{{
					Name:       metricsPortName,
					Port:       metricsPort,
					TargetPort: intstr.FromString(metricsPortName),
					Protocol:   corev1.ProtocolTCP,
				}},
			},
		},
	}
}

// reconcileMetricsServices creates or removes the metrics Services according to
// the serviceMonitor setting.
func (r *JumpstarterReconciler) reconcileMetricsServices(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	if !serviceMonitorEnabled(js) {
		for _, name := range []string{metricsServiceName(js, "controller"), metricsServiceName(js, "router")} {
			if err := r.deleteObjectIfExists(ctx, types.NamespacedName{Name: name, Namespace: js.Namespace}, &corev1.Service{}); err != nil {
				return err
			}
		}
		return nil
	}

	for _, desired := range desiredMetricsServices(js) {
		existing := &corev1.Service{}
		err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, existing)
		if apierrors.IsNotFound(err) {
			if err := controllerutil.SetControllerReference(js, desired, r.Scheme); err != nil {
				return err
			}
			if err := r.Create(ctx, desired); err != nil {
				return fmt.Errorf("create metrics service %s: %w", desired.Name, err)
			}
			log.Info("Created metrics service", "name", desired.Name)
			continue
		}
		if err != nil {
			return err
		}
		if equality.Semantic.DeepEqual(existing.Labels, desired.Labels) &&
			equality.Semantic.DeepEqual(existing.Spec.Selector, desired.Spec.Selector) &&
			equality.Semantic.DeepEqual(existing.Spec.Ports, desired.Spec.Ports) {
			continue
		}
		existing.Labels = desired.Labels
		existing.Spec.Selector = desired.Spec.Selector
		existing.Spec.Ports = desired.Spec.Ports
		if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
			return err
		}
		if err := r.Update(ctx, existing); err != nil {
			return fmt.Errorf("update metrics service %s: %w", desired.Name, err)
		}
		log.Info("Updated metrics service", "name", desired.Name)
	}
	return nil
}

// serviceMonitorCRDState is the result of probing for the ServiceMonitor CRD
// once during a reconcile. Passing it to status and requeue logic avoids
// duplicate bounded Lists and keeps their decisions based on the same snapshot.
type serviceMonitorCRDState struct {
	checked   bool
	available bool
	err       error
}

// serviceMonitorCRDAvailable reports whether monitoring.coreos.com/v1 serves the
// ServiceMonitor kind. It uses a bounded List, mirroring the cert-manager CRD
// detection pattern, and treats meta.IsNoMatchError as "CRD absent".
func (r *JumpstarterReconciler) serviceMonitorCRDAvailable(ctx context.Context) (bool, error) {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind + "List",
	})
	if err := r.List(ctx, list, client.Limit(1)); err != nil {
		if apimeta.IsNoMatchError(err) {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// serviceMonitorComponents returns the components that should have a
// ServiceMonitor for this CR.
func serviceMonitorComponents(js *operatorv1alpha1.Jumpstarter) []string {
	components := []string{"controller", "router"}
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Enabled && telemetryReplicas(js.Spec.Telemetry) > 0 {
		components = append(components, "telemetry")
	}
	return components
}

// serviceMonitorSelector returns the Service labels a ServiceMonitor selects.
func serviceMonitorSelector(js *operatorv1alpha1.Jumpstarter, component string) map[string]string {
	if component == "telemetry" {
		return map[string]string{
			"component":  "telemetry",
			"controller": js.Name,
		}
	}
	return metricsServiceLabels(js, component)
}

// desiredServiceMonitors returns unstructured ServiceMonitor objects for the
// running workloads.
func desiredServiceMonitors(js *operatorv1alpha1.Jumpstarter) []*unstructured.Unstructured {
	var monitors []*unstructured.Unstructured
	for _, component := range serviceMonitorComponents(js) {
		name := metricsServiceName(js, component)
		monitor := &unstructured.Unstructured{}
		monitor.SetGroupVersionKind(schema.GroupVersionKind{
			Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
		})
		monitor.SetName(name)
		monitor.SetNamespace(js.Namespace)
		labels := metricsServiceLabels(js, component)
		if js.Spec.Telemetry != nil {
			maps.Copy(labels, js.Spec.Telemetry.Metrics.ServiceMonitorLabels)
		}
		monitor.SetLabels(labels)
		monitor.Object["spec"] = map[string]any{
			"namespaceSelector": map[string]any{
				"matchNames": []any{js.Namespace},
			},
			"selector": map[string]any{
				"matchLabels": toInterfaceMap(serviceMonitorSelector(js, component)),
			},
			"endpoints": []any{
				map[string]any{
					"port": metricsPortName,
					"path": "/metrics",
				},
			},
		}
		monitors = append(monitors, monitor)
	}
	return monitors
}

// toInterfaceMap converts map[string]string to map[string]any for
// unstructured objects.
func toInterfaceMap(in map[string]string) map[string]any {
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

// reconcileServiceMonitors creates, updates, or removes ServiceMonitors.
// Removal happens when the feature is disabled, a workload is no longer
// desired (for example telemetry is disabled), or the CRD is not installed.
func (r *JumpstarterReconciler) reconcileServiceMonitors(ctx context.Context, js *operatorv1alpha1.Jumpstarter, crdAvailable bool) error {
	log := logf.FromContext(ctx)

	if !crdAvailable {
		// The ServiceMonitor API is not served. Its custom resources are
		// removed with the CRD, so there is nothing left to prune.
		return nil
	}

	var desired []*unstructured.Unstructured
	if serviceMonitorEnabled(js) {
		desired = desiredServiceMonitors(js)
	}

	desiredNames := make(map[string]struct{}, len(desired))
	for _, desiredMonitor := range desired {
		desiredNames[desiredMonitor.GetName()] = struct{}{}
		existing := &unstructured.Unstructured{}
		existing.SetGroupVersionKind(desiredMonitor.GroupVersionKind())
		err := r.Get(ctx, types.NamespacedName{Name: desiredMonitor.GetName(), Namespace: desiredMonitor.GetNamespace()}, existing)
		if apierrors.IsNotFound(err) {
			if err := controllerutil.SetControllerReference(js, desiredMonitor, r.Scheme); err != nil {
				return err
			}
			if err := r.Create(ctx, desiredMonitor); err != nil {
				return fmt.Errorf("create servicemonitor %s: %w", desiredMonitor.GetName(), err)
			}
			log.Info("Created ServiceMonitor", "name", desiredMonitor.GetName())
			continue
		}
		if err != nil {
			return err
		}
		if equality.Semantic.DeepEqual(existing.Object["spec"], desiredMonitor.Object["spec"]) &&
			equality.Semantic.DeepEqual(existing.GetLabels(), desiredMonitor.GetLabels()) {
			continue
		}
		existing.Object["spec"] = desiredMonitor.Object["spec"]
		existing.SetLabels(desiredMonitor.GetLabels())
		if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
			return err
		}
		if err := r.Update(ctx, existing); err != nil {
			return fmt.Errorf("update servicemonitor %s: %w", desiredMonitor.GetName(), err)
		}
		log.Info("Updated ServiceMonitor", "name", desiredMonitor.GetName())
	}

	return r.pruneServiceMonitors(ctx, js, desiredNames)
}

// pruneServiceMonitors deletes ServiceMonitors owned by this Jumpstarter CR
// that are no longer desired. Ownership is checked with the controller
// reference, not labels, because serviceMonitorLabels may override the
// operator's labels.
func (r *JumpstarterReconciler) pruneServiceMonitors(ctx context.Context, js *operatorv1alpha1.Jumpstarter, desired map[string]struct{}) error {
	log := logf.FromContext(ctx)

	monitors := &unstructured.UnstructuredList{}
	monitors.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind + "List",
	})
	if err := r.List(ctx, monitors, client.InNamespace(js.Namespace)); err != nil {
		return fmt.Errorf("list servicemonitors: %w", err)
	}

	for i := range monitors.Items {
		monitor := &monitors.Items[i]
		if _, ok := desired[monitor.GetName()]; ok {
			continue
		}
		if !metav1.IsControlledBy(monitor, js) {
			continue
		}

		monitor.SetGroupVersionKind(schema.GroupVersionKind{
			Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
		})
		if err := r.Delete(ctx, monitor); err != nil && !apierrors.IsNotFound(err) && !apimeta.IsNoMatchError(err) {
			return fmt.Errorf("delete stale servicemonitor %s: %w", monitor.GetName(), err)
		}
		log.Info("Deleted stale ServiceMonitor", "name", monitor.GetName())
	}

	return nil
}

// deleteObjectIfExists deletes an object and ignores NotFound.
func (r *JumpstarterReconciler) deleteObjectIfExists(ctx context.Context, key types.NamespacedName, obj client.Object) error {
	if err := r.Get(ctx, key, obj); err != nil {
		if apierrors.IsNotFound(err) || apimeta.IsNoMatchError(err) {
			return nil
		}
		return err
	}
	if err := r.Delete(ctx, obj); err != nil && !apierrors.IsNotFound(err) && !apimeta.IsNoMatchError(err) {
		return err
	}
	return nil
}

// reconcileObservability reconciles metrics Services, ServiceMonitors and the
// log shipper ConfigMap. It never blocks lease operations: each optional
// integration is attempted independently, failures are collected for retry, and
// the caller reports them through status conditions instead of aborting the
// Jumpstarter reconcile.
func (r *JumpstarterReconciler) reconcileObservability(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	_, err := r.reconcileObservabilityWithState(ctx, js)
	return err
}

func (r *JumpstarterReconciler) reconcileObservabilityWithState(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (serviceMonitorCRDState, error) {
	var errs []error
	var crdState serviceMonitorCRDState

	if err := r.reconcileMetricsServices(ctx, js); err != nil {
		errs = append(errs, fmt.Errorf("metrics services: %w", err))
	}

	crdAvailable, err := r.serviceMonitorCRDAvailable(ctx)
	crdState = serviceMonitorCRDState{checked: true, available: crdAvailable, err: err}
	if err != nil {
		// A transient API/RBAC error must not be mistaken for an absent CRD:
		// deleting the monitors here would cause churn and data loss.
		logf.FromContext(ctx).Error(err, "Failed to detect ServiceMonitor CRD; leaving ServiceMonitors unchanged")
		errs = append(errs, fmt.Errorf("service monitor CRD detection: %w", err))
	} else if err := r.reconcileServiceMonitors(ctx, js, crdAvailable); err != nil {
		errs = append(errs, fmt.Errorf("service monitors: %w", err))
	}

	if err := r.reconcileLogShipper(ctx, js); err != nil {
		errs = append(errs, fmt.Errorf("log shipper: %w", err))
	}

	return crdState, errors.Join(errs...)
}

// serviceMonitorCondition reports the ServiceMonitorReady condition state.
func (r *JumpstarterReconciler) serviceMonitorCondition(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string, string) {
	return r.serviceMonitorConditionWithState(ctx, js, serviceMonitorCRDState{})
}

func (r *JumpstarterReconciler) serviceMonitorConditionWithState(ctx context.Context, js *operatorv1alpha1.Jumpstarter, crdState serviceMonitorCRDState) (bool, string, string) {
	if !serviceMonitorEnabled(js) {
		return false, "Disabled", "Prometheus autodiscovery is disabled by spec.telemetry.metrics.serviceMonitor"
	}
	if !crdState.checked {
		available, err := r.serviceMonitorCRDAvailable(ctx)
		crdState = serviceMonitorCRDState{checked: true, available: available, err: err}
	}
	if crdState.err != nil {
		return false, "Failed", fmt.Sprintf("failed to detect ServiceMonitor CRD: %v", crdState.err)
	}
	if !crdState.available {
		return false, "CRDNotFound", "monitoring.coreos.com/v1 ServiceMonitor CRD is not installed"
	}
	for _, monitor := range desiredServiceMonitors(js) {
		existing := &unstructured.Unstructured{}
		existing.SetGroupVersionKind(monitor.GroupVersionKind())
		if err := r.Get(ctx, types.NamespacedName{Name: monitor.GetName(), Namespace: monitor.GetNamespace()}, existing); err != nil {
			return false, "Failed", fmt.Sprintf("ServiceMonitor %s is not available: %v", monitor.GetName(), err)
		}
	}
	return true, "Created", fmt.Sprintf("ServiceMonitors are configured for %d workloads", len(desiredServiceMonitors(js)))
}
