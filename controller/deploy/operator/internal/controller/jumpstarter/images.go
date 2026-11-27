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
	"fmt"
	"os"
	"strings"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// OperandImages are the images the operator deploys for a Jumpstarter that
// does not name its own. The operator Deployment passes them in RELATED_IMAGE_*
// variables, which the bundle and the installer pin to the operator's own tag,
// so an operator always deploys components from its own release.
type OperandImages struct {
	// Controller runs the controller, the routers and telemetry.
	Controller string
	// ExporterSetController runs the ExporterSet provisioner controllers.
	ExporterSetController string
}

const (
	controllerImageEnv            = "RELATED_IMAGE_CONTROLLER"
	exporterSetControllerImageEnv = "RELATED_IMAGE_EXPORTERSET_CONTROLLER"
)

// legacyControllerImageDefault was the CRD default for spec.controller.image
// and spec.routers.image up to v0.9.0. The API server stored it in every
// Jumpstarter created without an image, so it stands for "unset", not for a
// request to follow :latest.
const legacyControllerImageDefault = "quay.io/jumpstarter-dev/jumpstarter-controller:latest"

// OperandImagesFromEnv reads the operand images from the operator's environment.
func OperandImagesFromEnv() (OperandImages, error) {
	images := OperandImages{
		Controller:            os.Getenv(controllerImageEnv),
		ExporterSetController: os.Getenv(exporterSetControllerImageEnv),
	}
	var missing []string
	if images.Controller == "" {
		missing = append(missing, controllerImageEnv)
	}
	if images.ExporterSetController == "" {
		missing = append(missing, exporterSetControllerImageEnv)
	}
	if len(missing) > 0 {
		return OperandImages{}, fmt.Errorf("operand images not set: %s", strings.Join(missing, ", "))
	}
	return images, nil
}

// applyDefaults fills in the images the spec leaves unset. Like the other
// runtime defaults, it changes only the in-memory object: the operator never
// writes a Jumpstarter spec back.
func (images OperandImages) applyDefaults(spec *operatorv1alpha1.JumpstarterSpec) {
	if spec.Controller.Image == "" || spec.Controller.Image == legacyControllerImageDefault {
		spec.Controller.Image = images.Controller
	}
	if spec.Routers.Image == "" || spec.Routers.Image == legacyControllerImageDefault {
		spec.Routers.Image = images.Controller
	}
	if spec.Telemetry != nil && spec.Telemetry.Image == "" {
		spec.Telemetry.Image = images.Controller
	}
	if spec.ExporterSets != nil && spec.ExporterSets.Image == "" {
		spec.ExporterSets.Image = images.ExporterSetController
	}
}
