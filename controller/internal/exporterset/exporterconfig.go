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

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"gopkg.in/yaml.v3"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
)

const (
	// configSecretPrefix is used to name the ExporterConfig Secret.
	configSecretPrefix = "exporter-config-"

	// caConfigMapName is the well-known ConfigMap containing the service CA bundle.
	caConfigMapName = "jumpstarter-service-ca-cert"
	caConfigMapKey  = "ca.crt"

	// exporterConfigKey is the key in the Secret data map.
	exporterConfigKey = "config.yaml"
)

// exporterConfig represents the YAML structure of a Jumpstarter ExporterConfig.
type exporterConfig struct {
	APIVersion     string                       `yaml:"apiVersion"`
	Kind           string                       `yaml:"kind"`
	Metadata       exporterConfigMetadata       `yaml:"metadata"`
	Endpoint       string                       `yaml:"endpoint"`
	TLS            exporterConfigTLS            `yaml:"tls"`
	Token          string                       `yaml:"token"`
	Export         map[string]exporterConfigDriver `yaml:"export"`
	ExitOnLeaseEnd bool                         `yaml:"exitOnLeaseEnd"`
}

type exporterConfigMetadata struct {
	Name      string `yaml:"name"`
	Namespace string `yaml:"namespace"`
}

type exporterConfigTLS struct {
	CA string `yaml:"ca"`
}

type exporterConfigDriver struct {
	Type     string                 `yaml:"type"`
	Config   map[string]interface{} `yaml:"config,omitempty"`
	Children map[string]exporterConfigDriver `yaml:"children,omitempty"`
}

// buildExporterConfigSecret builds a Secret containing the ExporterConfig YAML
// for the given exporter instance. It reads the token from the credential Secret
// and the CA from the well-known ConfigMap.
func (r *ExporterSetReconciler) buildExporterConfigSecret(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
	mergedParameters map[string]interface{},
) (*corev1.Secret, error) {
	// Fetch token from credential Secret.
	if exporter.Status.Credential == nil {
		return nil, fmt.Errorf("exporter %s has no credential reference", exporter.Name)
	}

	credSecret := &corev1.Secret{}
	if err := r.Get(ctx, client.ObjectKey{
		Namespace: exporter.Namespace,
		Name:      exporter.Status.Credential.Name,
	}, credSecret); err != nil {
		return nil, fmt.Errorf("unable to get credential Secret %s: %w",
			exporter.Status.Credential.Name, err)
	}

	token := string(credSecret.Data["token"])
	if token == "" {
		return nil, fmt.Errorf("credential Secret %s has no 'token' key",
			exporter.Status.Credential.Name)
	}

	// Fetch CA from the well-known ConfigMap.
	caCM := &corev1.ConfigMap{}
	if err := r.Get(ctx, client.ObjectKey{
		Namespace: exporter.Namespace,
		Name:      caConfigMapName,
	}, caCM); err != nil {
		return nil, fmt.Errorf("unable to get CA ConfigMap %s: %w", caConfigMapName, err)
	}

	caPEM := caCM.Data[caConfigMapKey]
	if caPEM == "" {
		return nil, fmt.Errorf("CA ConfigMap %s has no '%s' key", caConfigMapName, caConfigMapKey)
	}
	caBase64 := base64.StdEncoding.EncodeToString([]byte(caPEM))

	// Build export map from template drivers, enriched by the provisioner.
	drivers := es.Spec.Template.Spec.Drivers
	drivers = r.Provisioner.EnrichExporterExport(drivers, mergedParameters)

	exportMap := buildExportMap(drivers)

	cfg := exporterConfig{
		APIVersion: "jumpstarter.dev/v1alpha1",
		Kind:       "ExporterConfig",
		Metadata: exporterConfigMetadata{
			Name:      exporter.Name,
			Namespace: exporter.Namespace,
		},
		Endpoint: exporter.Status.Endpoint,
		TLS: exporterConfigTLS{
			CA: caBase64,
		},
		Token:          token,
		Export:         exportMap,
		ExitOnLeaseEnd: true,
	}

	cfgYAML, err := yaml.Marshal(cfg)
	if err != nil {
		return nil, fmt.Errorf("unable to marshal ExporterConfig: %w", err)
	}

	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      configSecretPrefix + exporter.Name,
			Namespace: exporter.Namespace,
			Labels: map[string]string{
				labelExporterSetName: es.Name,
			},
		},
		Data: map[string][]byte{
			exporterConfigKey: cfgYAML,
		},
	}

	return secret, nil
}

// buildExportMap converts a slice of DriverConfigs into the export map
// used by the ExporterConfig YAML.
func buildExportMap(drivers []virtualtargetv1alpha1.DriverConfig) map[string]exporterConfigDriver {
	exportMap := make(map[string]exporterConfigDriver, len(drivers))

	for _, d := range drivers {
		name := d.Name
		if name == "" {
			name = deriveDriverName(d.Type)
		}

		var config map[string]interface{}
		if d.Config != nil && d.Config.Raw != nil {
			_ = json.Unmarshal(d.Config.Raw, &config)
		}

		exportMap[name] = exporterConfigDriver{
			Type:   d.Type,
			Config: config,
		}
	}

	return exportMap
}

// deriveDriverName extracts a short name from a fully qualified Python class name.
// e.g. "jumpstarter_driver_qemu.driver.Qemu" -> "Qemu"
func deriveDriverName(fqn string) string {
	parts := strings.Split(fqn, ".")
	if len(parts) == 0 {
		return fqn
	}
	return parts[len(parts)-1]
}
