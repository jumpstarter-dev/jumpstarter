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

package exporterset

// OrphanedResourcesError reports that a provisioner's Cleanup could not reach
// the remote host (for example the credentials Secret was deleted or the host
// was unreachable) and may have left resources behind. Cleanup returns it
// instead of an ordinary error so the reconciler can emit a Warning Event and
// still clear the finalizer: retrying would not help, and wedging the
// ExporterSet on an unreachable host is worse than leaving the operator to
// remove the resources by hand.
type OrphanedResourcesError struct {
	// Message names the host and the resources that may remain.
	Message string
}

func (e *OrphanedResourcesError) Error() string {
	return e.Message
}
