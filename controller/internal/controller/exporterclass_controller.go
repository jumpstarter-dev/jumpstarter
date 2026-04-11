/*
Copyright 2024.

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

package controller

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
)

// ExporterClassReconciler reconciles ExporterClass objects.
type ExporterClassReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporterclasses,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporterclasses/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporterclasses/finalizers,verbs=update
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=driverinterfaces,verbs=get;list;watch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=driverinterfaces/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters,verbs=get;list;watch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters/status,verbs=get;update;patch

func (r *ExporterClassReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	var exporterClass jumpstarterdevv1alpha1.ExporterClass
	if err := r.Get(ctx, req.NamespacedName, &exporterClass); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(
			fmt.Errorf("Reconcile: unable to get ExporterClass: %w", err),
		)
	}

	original := client.MergeFrom(exporterClass.DeepCopy())

	// Step 1: Resolve the extends chain and flatten interfaces.
	resolvedInterfaces, err := r.resolveInterfaces(ctx, &exporterClass)
	if err != nil {
		meta.SetStatusCondition(&exporterClass.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterClassConditionTypeDegraded),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: exporterClass.Generation,
			Reason:             "ResolutionFailed",
			Message:            err.Error(),
		})
		meta.SetStatusCondition(&exporterClass.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterClassConditionTypeReady),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporterClass.Generation,
			Reason:             "ResolutionFailed",
			Message:            err.Error(),
		})
		if patchErr := r.Status().Patch(ctx, &exporterClass, original); patchErr != nil {
			return RequeueConflict(logger, ctrl.Result{}, patchErr)
		}
		return ctrl.Result{}, nil
	}

	// Collect resolved interface ref names for status.
	var resolvedInterfaceNames []string
	for _, iface := range resolvedInterfaces {
		resolvedInterfaceNames = append(resolvedInterfaceNames, iface.InterfaceRef)
	}
	exporterClass.Status.ResolvedInterfaces = resolvedInterfaceNames

	// Step 2: Verify all referenced DriverInterfaces exist.
	driverInterfaces := make(map[string]*jumpstarterdevv1alpha1.DriverInterface)
	var missingRefs []string
	for _, iface := range resolvedInterfaces {
		var di jumpstarterdevv1alpha1.DriverInterface
		if err := r.Get(ctx, types.NamespacedName{
			Name:      iface.InterfaceRef,
			Namespace: exporterClass.Namespace,
		}, &di); err != nil {
			if client.IgnoreNotFound(err) == nil {
				missingRefs = append(missingRefs, iface.InterfaceRef)
				continue
			}
			return ctrl.Result{}, fmt.Errorf("failed to get DriverInterface %s: %w", iface.InterfaceRef, err)
		}
		driverInterfaces[iface.InterfaceRef] = &di
	}

	if len(missingRefs) > 0 {
		msg := fmt.Sprintf("missing DriverInterface(s): %s", strings.Join(missingRefs, ", "))
		meta.SetStatusCondition(&exporterClass.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterClassConditionTypeDegraded),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: exporterClass.Generation,
			Reason:             "MissingDriverInterface",
			Message:            msg,
		})
		meta.SetStatusCondition(&exporterClass.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterClassConditionTypeReady),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporterClass.Generation,
			Reason:             "MissingDriverInterface",
			Message:            msg,
		})
		if patchErr := r.Status().Patch(ctx, &exporterClass, original); patchErr != nil {
			return RequeueConflict(logger, ctrl.Result{}, patchErr)
		}
		return ctrl.Result{}, nil
	}

	// Clear Degraded condition since resolution succeeded.
	meta.RemoveStatusCondition(&exporterClass.Status.Conditions,
		string(jumpstarterdevv1alpha1.ExporterClassConditionTypeDegraded))

	// Step 3: Evaluate all exporters against this ExporterClass.
	satisfiedCount, err := r.evaluateExporters(ctx, &exporterClass, resolvedInterfaces, driverInterfaces)
	if err != nil {
		return ctrl.Result{}, fmt.Errorf("failed to evaluate exporters: %w", err)
	}

	exporterClass.Status.SatisfiedExporterCount = satisfiedCount

	meta.SetStatusCondition(&exporterClass.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.ExporterClassConditionTypeReady),
		Status:             metav1.ConditionTrue,
		ObservedGeneration: exporterClass.Generation,
		Reason:             "ExportersSatisfied",
		Message:            fmt.Sprintf("%d exporters satisfy all required interfaces", satisfiedCount),
	})

	if err := r.Status().Patch(ctx, &exporterClass, original); err != nil {
		return RequeueConflict(logger, ctrl.Result{}, err)
	}

	return ctrl.Result{}, nil
}

// resolveInterfaces flattens the extends chain and returns the full list of
// InterfaceRequirements, detecting circular inheritance.
func (r *ExporterClassReconciler) resolveInterfaces(
	ctx context.Context,
	ec *jumpstarterdevv1alpha1.ExporterClass,
) ([]jumpstarterdevv1alpha1.InterfaceRequirement, error) {
	visited := map[string]bool{}
	return r.resolveInterfacesRecursive(ctx, ec, visited)
}

func (r *ExporterClassReconciler) resolveInterfacesRecursive(
	ctx context.Context,
	ec *jumpstarterdevv1alpha1.ExporterClass,
	visited map[string]bool,
) ([]jumpstarterdevv1alpha1.InterfaceRequirement, error) {
	key := ec.Namespace + "/" + ec.Name
	if visited[key] {
		return nil, fmt.Errorf("circular extends chain detected at ExporterClass %q", ec.Name)
	}
	visited[key] = true

	var inherited []jumpstarterdevv1alpha1.InterfaceRequirement

	if ec.Spec.Extends != "" {
		var parent jumpstarterdevv1alpha1.ExporterClass
		if err := r.Get(ctx, types.NamespacedName{
			Name:      ec.Spec.Extends,
			Namespace: ec.Namespace,
		}, &parent); err != nil {
			return nil, fmt.Errorf("parent ExporterClass %q not found: %w", ec.Spec.Extends, err)
		}
		parentInterfaces, err := r.resolveInterfacesRecursive(ctx, &parent, visited)
		if err != nil {
			return nil, err
		}
		inherited = parentInterfaces
	}

	// Merge: child interfaces override parent interfaces with the same name.
	merged := make(map[string]jumpstarterdevv1alpha1.InterfaceRequirement)
	for _, iface := range inherited {
		merged[iface.Name] = iface
	}
	for _, iface := range ec.Spec.Interfaces {
		merged[iface.Name] = iface
	}

	result := make([]jumpstarterdevv1alpha1.InterfaceRequirement, 0, len(merged))
	for _, iface := range merged {
		result = append(result, iface)
	}

	return result, nil
}

// evaluateExporters checks all exporters in the namespace against the
// ExporterClass's selector and interface requirements. Returns the count of
// satisfied exporters and updates each exporter's SatisfiedExporterClasses
// and ExporterClassCompliance condition.
func (r *ExporterClassReconciler) evaluateExporters(
	ctx context.Context,
	ec *jumpstarterdevv1alpha1.ExporterClass,
	resolvedInterfaces []jumpstarterdevv1alpha1.InterfaceRequirement,
	driverInterfaces map[string]*jumpstarterdevv1alpha1.DriverInterface,
) (int, error) {
	var exporterList jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporterList, client.InNamespace(ec.Namespace)); err != nil {
		return 0, fmt.Errorf("failed to list exporters: %w", err)
	}

	// Build label selector from ExporterClass spec.
	var selector labels.Selector
	if ec.Spec.Selector != nil {
		var err error
		selector, err = metav1.LabelSelectorAsSelector(ec.Spec.Selector)
		if err != nil {
			return 0, fmt.Errorf("invalid label selector: %w", err)
		}
	}

	satisfiedCount := 0

	for i := range exporterList.Items {
		exporter := &exporterList.Items[i]

		// Check if selector matches.
		if selector != nil && !selector.Matches(labels.Set(exporter.Labels)) {
			continue
		}

		// Exporter labels match — validate interfaces.
		missingInterfaces := r.validateExporterInterfaces(exporter, resolvedInterfaces, driverInterfaces)

		exporterOriginal := client.MergeFrom(exporter.DeepCopy())

		if len(missingInterfaces) == 0 {
			// Exporter satisfies this ExporterClass.
			satisfiedCount++
			if !slices.Contains(exporter.Status.SatisfiedExporterClasses, ec.Name) {
				exporter.Status.SatisfiedExporterClasses = append(
					exporter.Status.SatisfiedExporterClasses, ec.Name)
			}
		} else {
			// Remove from satisfied list if previously satisfied.
			exporter.Status.SatisfiedExporterClasses = removeString(
				exporter.Status.SatisfiedExporterClasses, ec.Name)
		}

		// Update ExporterClassCompliance condition.
		r.updateComplianceCondition(exporter, ec.Name, missingInterfaces)

		if err := r.Status().Patch(ctx, exporter, exporterOriginal); err != nil {
			// Log but don't fail the whole reconciliation for a single exporter patch error.
			log.FromContext(ctx).Error(err, "failed to patch exporter status",
				"exporter", exporter.Name)
		}
	}

	return satisfiedCount, nil
}

// validateExporterInterfaces checks whether an exporter's devices provide all
// required interfaces. Returns the list of missing required interface names.
func (r *ExporterClassReconciler) validateExporterInterfaces(
	exporter *jumpstarterdevv1alpha1.Exporter,
	resolvedInterfaces []jumpstarterdevv1alpha1.InterfaceRequirement,
	driverInterfaces map[string]*jumpstarterdevv1alpha1.DriverInterface,
) []string {
	// Build a set of proto packages provided by the exporter's devices.
	providedPackages := make(map[string]bool)
	for _, device := range exporter.Status.Devices {
		if len(device.FileDescriptorProto) > 0 {
			// Parse the FileDescriptorProto to get the package name.
			pkg := ExtractProtoPackage(device.FileDescriptorProto)
			if pkg != "" {
				providedPackages[pkg] = true
			}
		}
		// Also check device labels for the interface package name.
		if pkgLabel, ok := device.Labels["jumpstarter.dev/interface"]; ok {
			providedPackages[pkgLabel] = true
		}
	}

	var missing []string
	for _, iface := range resolvedInterfaces {
		if !iface.Required {
			continue
		}
		di, ok := driverInterfaces[iface.InterfaceRef]
		if !ok {
			missing = append(missing, iface.Name)
			continue
		}
		if !providedPackages[di.Spec.Proto.Package] {
			missing = append(missing, iface.Name)
		}
	}

	return missing
}

// updateComplianceCondition sets the ExporterClassCompliance condition on an exporter.
func (r *ExporterClassReconciler) updateComplianceCondition(
	exporter *jumpstarterdevv1alpha1.Exporter,
	exporterClassName string,
	missingInterfaces []string,
) {
	if len(missingInterfaces) == 0 {
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeExporterClassCompliance),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: exporter.Generation,
			Reason:             "Satisfied",
			Message:            fmt.Sprintf("ExporterClass '%s': all required interfaces satisfied", exporterClassName),
		})
	} else {
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeExporterClassCompliance),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporter.Generation,
			Reason:             "InterfaceMismatch",
			Message: fmt.Sprintf("ExporterClass '%s': missing required interface(s): %s",
				exporterClassName, strings.Join(missingInterfaces, ", ")),
		})
	}
}

// ExtractProtoPackage extracts the package name from a serialized FileDescriptorProto.
// FileDescriptorProto field 2 is the package (string).
func ExtractProtoPackage(data []byte) string {
	// Minimal protobuf wire-format parser for field 2 (length-delimited string).
	// Field 2, wire type 2 (length-delimited) = tag byte 0x12.
	i := 0
	for i < len(data) {
		if i >= len(data) {
			break
		}
		tag := data[i]
		fieldNumber := tag >> 3
		wireType := tag & 0x07
		i++

		switch wireType {
		case 0: // varint
			for i < len(data) && data[i]&0x80 != 0 {
				i++
			}
			i++ // skip last byte of varint
		case 2: // length-delimited
			if i >= len(data) {
				return ""
			}
			length := int(data[i])
			i++
			if fieldNumber == 2 && i+length <= len(data) {
				return string(data[i : i+length])
			}
			i += length
		default:
			// Unknown wire type, bail out.
			return ""
		}
	}
	return ""
}

func removeString(slice []string, s string) []string {
	result := make([]string, 0, len(slice))
	for _, item := range slice {
		if item != s {
			result = append(result, item)
		}
	}
	return result
}

// SetupWithManager sets up the controller with the Manager.
func (r *ExporterClassReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.ExporterClass{}).
		// Re-reconcile ExporterClasses when Exporters change (devices may affect compliance).
		Watches(&jumpstarterdevv1alpha1.Exporter{},
			handler.EnqueueRequestsFromMapFunc(r.exporterToExporterClasses)).
		// Re-reconcile when DriverInterfaces change.
		Watches(&jumpstarterdevv1alpha1.DriverInterface{},
			handler.EnqueueRequestsFromMapFunc(r.driverInterfaceToExporterClasses)).
		Complete(r)
}

// exporterToExporterClasses maps an Exporter change to all ExporterClasses in
// the same namespace for re-evaluation.
func (r *ExporterClassReconciler) exporterToExporterClasses(
	ctx context.Context,
	obj client.Object,
) []reconcile.Request {
	var ecList jumpstarterdevv1alpha1.ExporterClassList
	if err := r.List(ctx, &ecList, client.InNamespace(obj.GetNamespace())); err != nil {
		log.FromContext(ctx).Error(err, "failed to list ExporterClasses for exporter mapping")
		return nil
	}
	requests := make([]reconcile.Request, len(ecList.Items))
	for i, ec := range ecList.Items {
		requests[i] = reconcile.Request{
			NamespacedName: types.NamespacedName{
				Name:      ec.Name,
				Namespace: ec.Namespace,
			},
		}
	}
	return requests
}

// driverInterfaceToExporterClasses maps a DriverInterface change to all
// ExporterClasses that reference it.
func (r *ExporterClassReconciler) driverInterfaceToExporterClasses(
	ctx context.Context,
	obj client.Object,
) []reconcile.Request {
	diName := obj.GetName()
	var ecList jumpstarterdevv1alpha1.ExporterClassList
	if err := r.List(ctx, &ecList, client.InNamespace(obj.GetNamespace())); err != nil {
		log.FromContext(ctx).Error(err, "failed to list ExporterClasses for DriverInterface mapping")
		return nil
	}
	var requests []reconcile.Request
	for _, ec := range ecList.Items {
		for _, iface := range ec.Spec.Interfaces {
			if iface.InterfaceRef == diName {
				requests = append(requests, reconcile.Request{
					NamespacedName: types.NamespacedName{
						Name:      ec.Name,
						Namespace: ec.Namespace,
					},
				})
				break
			}
		}
	}
	return requests
}
