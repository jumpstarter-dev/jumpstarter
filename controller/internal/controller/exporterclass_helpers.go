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

	"k8s.io/apimachinery/pkg/labels"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
)

// ResolveInterfaces flattens the extends chain and returns the full list of
// InterfaceRequirements for an ExporterClass. This is the exported version
// of the reconciler method, usable from RPC handlers.
func ResolveInterfaces(
	ctx context.Context,
	k8sClient client.Client,
	ec *jumpstarterdevv1alpha1.ExporterClass,
) ([]jumpstarterdevv1alpha1.InterfaceRequirement, error) {
	visited := map[string]bool{}
	return resolveInterfacesRecursiveStandalone(ctx, k8sClient, ec, visited)
}

func resolveInterfacesRecursiveStandalone(
	ctx context.Context,
	k8sClient client.Client,
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
		if err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      ec.Spec.Extends,
			Namespace: ec.Namespace,
		}, &parent); err != nil {
			return nil, fmt.Errorf("parent ExporterClass %q not found: %w", ec.Spec.Extends, err)
		}
		parentInterfaces, err := resolveInterfacesRecursiveStandalone(ctx, k8sClient, &parent, visited)
		if err != nil {
			return nil, err
		}
		inherited = parentInterfaces
	}

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

// ValidateDevicesAgainstInterfaces checks whether the provided devices satisfy
// the resolved interface requirements. Returns per-interface validation results.
func ValidateDevicesAgainstInterfaces(
	devices []jumpstarterdevv1alpha1.Device,
	resolvedInterfaces []jumpstarterdevv1alpha1.InterfaceRequirement,
	driverInterfaces map[string]*jumpstarterdevv1alpha1.DriverInterface,
) []*InterfaceValidationDetail {
	// Build a set of proto packages provided by the devices.
	providedPackages := make(map[string]bool)
	for _, device := range devices {
		if len(device.FileDescriptorProto) > 0 {
			pkg := ExtractProtoPackage(device.FileDescriptorProto)
			if pkg != "" {
				providedPackages[pkg] = true
			}
		}
		if pkgLabel, ok := device.Labels["jumpstarter.dev/interface"]; ok {
			providedPackages[pkgLabel] = true
		}
	}

	var results []*InterfaceValidationDetail
	for _, iface := range resolvedInterfaces {
		detail := &InterfaceValidationDetail{
			Name:         iface.Name,
			InterfaceRef: iface.InterfaceRef,
			Required:     iface.Required,
		}

		di, ok := driverInterfaces[iface.InterfaceRef]
		if !ok {
			detail.Found = false
			detail.ErrorMessage = fmt.Sprintf("DriverInterface %q not found", iface.InterfaceRef)
			results = append(results, detail)
			continue
		}

		detail.Found = providedPackages[di.Spec.Proto.Package]
		if detail.Found {
			detail.StructurallyCompatible = true // TODO: structural validation in future phase
		} else if iface.Required {
			detail.ErrorMessage = fmt.Sprintf("required interface %q (package %s) not found in exporter devices",
				iface.Name, di.Spec.Proto.Package)
		}
		results = append(results, detail)
	}

	return results
}

// InterfaceValidationDetail holds per-interface validation results.
type InterfaceValidationDetail struct {
	Name                   string
	InterfaceRef           string
	Required               bool
	Found                  bool
	StructurallyCompatible bool
	ErrorMessage           string
}

// MatchExporterClassesByLabels returns ExporterClasses in the given namespace
// whose selector matches the provided labels.
func MatchExporterClassesByLabels(
	ctx context.Context,
	k8sClient client.Client,
	namespace string,
	exporterLabels map[string]string,
) ([]jumpstarterdevv1alpha1.ExporterClass, error) {
	var ecList jumpstarterdevv1alpha1.ExporterClassList
	if err := k8sClient.List(ctx, &ecList, client.InNamespace(namespace)); err != nil {
		return nil, fmt.Errorf("failed to list ExporterClasses: %w", err)
	}

	var matched []jumpstarterdevv1alpha1.ExporterClass
	for _, ec := range ecList.Items {
		if ec.Spec.Selector != nil {
			selector, err := metav1.LabelSelectorAsSelector(ec.Spec.Selector)
			if err != nil {
				continue
			}
			if !selector.Matches(labels.Set(exporterLabels)) {
				continue
			}
		}
		matched = append(matched, ec)
	}

	return matched, nil
}

// FetchDriverInterfaces resolves all DriverInterface objects referenced by the
// given interface requirements. Returns a map keyed by interfaceRef name.
func FetchDriverInterfaces(
	ctx context.Context,
	k8sClient client.Client,
	namespace string,
	resolvedInterfaces []jumpstarterdevv1alpha1.InterfaceRequirement,
) (map[string]*jumpstarterdevv1alpha1.DriverInterface, []string) {
	driverInterfaces := make(map[string]*jumpstarterdevv1alpha1.DriverInterface)
	var missingRefs []string

	for _, iface := range resolvedInterfaces {
		if _, exists := driverInterfaces[iface.InterfaceRef]; exists {
			continue
		}
		var di jumpstarterdevv1alpha1.DriverInterface
		if err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      iface.InterfaceRef,
			Namespace: namespace,
		}, &di); err != nil {
			if client.IgnoreNotFound(err) == nil {
				missingRefs = append(missingRefs, iface.InterfaceRef)
				continue
			}
			missingRefs = append(missingRefs, iface.InterfaceRef+" (error: "+err.Error()+")")
			continue
		}
		driverInterfaces[iface.InterfaceRef] = &di
	}

	return driverInterfaces, missingRefs
}

// IsSatisfied returns true if all required interfaces are found.
func IsSatisfied(results []*InterfaceValidationDetail) bool {
	for _, r := range results {
		if r.Required && !r.Found {
			return false
		}
	}
	return true
}

// CollectDriverInterfacesForExporter collects all unique DriverInterface objects
// relevant to an exporter's devices. Returns them keyed by name.
func CollectDriverInterfacesForExporter(
	ctx context.Context,
	k8sClient client.Client,
	namespace string,
	devices []jumpstarterdevv1alpha1.Device,
) ([]jumpstarterdevv1alpha1.DriverInterface, error) {
	// Get all proto packages from the exporter's devices.
	providedPackages := make(map[string]bool)
	for _, device := range devices {
		if len(device.FileDescriptorProto) > 0 {
			pkg := ExtractProtoPackage(device.FileDescriptorProto)
			if pkg != "" {
				providedPackages[pkg] = true
			}
		}
		if pkgLabel, ok := device.Labels["jumpstarter.dev/interface"]; ok {
			providedPackages[pkgLabel] = true
		}
	}

	// List all DriverInterfaces in namespace and filter by matching packages.
	var diList jumpstarterdevv1alpha1.DriverInterfaceList
	if err := k8sClient.List(ctx, &diList, client.InNamespace(namespace)); err != nil {
		return nil, fmt.Errorf("failed to list DriverInterfaces: %w", err)
	}

	var matched []jumpstarterdevv1alpha1.DriverInterface
	seen := make(map[string]bool)
	for _, di := range diList.Items {
		if providedPackages[di.Spec.Proto.Package] && !seen[di.Name] {
			matched = append(matched, di)
			seen[di.Name] = true
		}
	}

	return matched, nil
}
