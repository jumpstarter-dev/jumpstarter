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

package v1alpha1

// EffectiveStorageClassName returns the StorageClass to use for guest disk
// volumes. ExporterSet.spec.storageClassName overrides the class when set
// (including the empty string to force emptyDir).
func EffectiveStorageClassName(vtc *VirtualTargetClass, es *ExporterSet) string {
	if es != nil && es.Spec.StorageClassName != nil {
		return *es.Spec.StorageClassName
	}
	if vtc != nil {
		return vtc.Spec.StorageClassName
	}
	return ""
}
