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

package qemussh

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"maps"
	"path/filepath"
	"strings"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	sigsyaml "sigs.k8s.io/yaml"

	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

const (
	// ProvisionerName is the provisioner identifier for
	// off-cluster QEMU targets deployed via SSH.
	ProvisionerName = "qemu-ssh.jumpstarter.dev"

	// DefaultExporterImage is the exporter container image.
	DefaultExporterImage = "quay.io/jumpstarter-dev/jumpstarter:latest"

	// DefaultQEMURuntimeImage is the QEMU runtime container image.
	DefaultQEMURuntimeImage = "quay.io/jumpstarter-dev/virtual/qemu-runtime:latest"

	// sshPrivateKeyField is the Secret data key for SSH private
	// keys (standard kubernetes.io/ssh-auth type).
	sshPrivateKeyField = "ssh-privatekey"
)

// Provisioner implements the qemu-ssh.jumpstarter.dev provisioner
// for off-cluster QEMU targets deployed via SSH to remote lab hosts.
//
// It implements both the exporterset.Provisioner interface (Name,
// RenderPod, EnrichExporterExport, Cleanup) and the
// exporterset.Deployer interface (Deploy, IsDeployed).
type Provisioner struct {
	Version string
	Client  client.Client
}

// New creates a new qemu-ssh provisioner.
func New(version string, c client.Client) *Provisioner {
	return &Provisioner{Version: version, Client: c}
}

// Name returns the provisioner identifier.
func (p *Provisioner) Name() string {
	return ProvisionerName
}

// RenderPod returns nil — off-cluster provisioners don't create Pods.
// The reconciler detects the Deployer interface and calls Deploy
// instead.
func (p *Provisioner) RenderPod(
	_ context.Context,
	_ *virtualtargetv1alpha1.ExporterSet,
	_ *virtualtargetv1alpha1.VirtualTargetClass,
	_ map[string]any,
	_ *virtualtargetv1alpha1.ImageOverrides,
	_ *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	return nil, nil
}

// EnrichExporterExport adjusts driver config for off-cluster
// deployment (launcher_socket, defaults, firmware paths, hostfwd).
func (p *Provisioner) EnrichExporterExport(
	_ context.Context,
	_ *virtualtargetv1alpha1.VirtualTargetClass,
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]any,
	_ *jumpstarterdevv1alpha1.Exporter,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	return enrichExporterExport(drivers, mergedParameters)
}

// Deploy sets up the exporter on a remote host via SSH:
//  1. Read SSH key from credentialsSecretRef
//  2. Select a host with free capacity
//  3. Connect via SSH
//  4. Write exporter config, quadlet files
//  5. Create shared volume, reload systemd, start containers
//  6. Annotate the Exporter CR with the host assignment
func (p *Provisioner) Deploy(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]any,
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
	caBundle string,
) error {
	logger := log.FromContext(ctx)

	privateKey, err := p.readSSHKey(ctx, vtc)
	if err != nil {
		return err
	}

	host, err := ParseHost(mergedParameters)
	if err != nil {
		return fmt.Errorf("parse host: %w", err)
	}

	logger.Info("deploying exporter to host",
		"exporter", exporter.Name,
		"host", host.Name,
	)

	conn, err := Connect(SSHConnectConfig{
		Host:       host.Name,
		Port:       host.Port,
		User:       host.User,
		PrivateKey: privateKey,
	})
	if err != nil {
		return fmt.Errorf("SSH connect to %s: %w", host.Name, err)
	}
	defer conn.Close() //nolint:errcheck

	if err := p.deployInstance(ctx, conn, es, mergedParameters, images, exporter, caBundle); err != nil {
		return err
	}

	credSecret := ""
	if vtc.Spec.CredentialsSecretRef != nil {
		credSecret = vtc.Spec.CredentialsSecretRef.Name
	}
	if err := p.annotateHost(ctx, exporter, host.Name, credSecret); err != nil {
		return fmt.Errorf("annotate exporter %s with host: %w", exporter.Name, err)
	}

	return nil
}

// IsDeployed checks whether the exporter has a host assignment
// annotation (meaning Deploy was previously called successfully).
func (p *Provisioner) IsDeployed(
	_ context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (bool, error) {
	if exporter.Annotations == nil {
		return false, nil
	}
	_, ok := exporter.Annotations[AnnotationHost]
	return ok, nil
}

// Cleanup tears down the remote containers via SSH and removes the
// host annotation.
//
// Must not return a hard error when the VirtualTargetClass is already
// gone: kubectl delete -f often removes the VTC before the ExporterSet
// finalizer runs, and a failing Cleanup would wedge the finalizer
// forever.
func (p *Provisioner) Cleanup(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	logger := log.FromContext(ctx)

	hostName := ""
	secretName := ""
	if exporter.Annotations != nil {
		hostName = exporter.Annotations[AnnotationHost]
		secretName = exporter.Annotations[AnnotationCredentialsSecret]
	}
	if hostName == "" {
		logger.V(1).Info("no host annotation on exporter, skipping cleanup",
			"exporter", exporter.Name)
		return nil
	}

	var classParams *apiextensionsv1.JSON
	vtcKey := client.ObjectKey{
		Namespace: es.Namespace,
		Name:      es.Spec.VirtualTargetClassName,
	}
	var vtc virtualtargetv1alpha1.VirtualTargetClass
	if err := p.Client.Get(ctx, vtcKey, &vtc); err != nil {
		if !apierrors.IsNotFound(err) {
			return fmt.Errorf("get VirtualTargetClass for cleanup: %w", err)
		}
		logger.Info("VirtualTargetClass already deleted, cleaning up from annotations",
			"exporter", exporter.Name, "vtc", es.Spec.VirtualTargetClassName)
	} else {
		classParams = vtc.Spec.Parameters
		if secretName == "" && vtc.Spec.CredentialsSecretRef != nil {
			secretName = vtc.Spec.CredentialsSecretRef.Name
		}
	}

	if secretName == "" {
		logger.Error(nil, "no SSH credentials secret for cleanup, skipping remote teardown",
			"exporter", exporter.Name)
		return nil
	}

	privateKey, err := p.readSSHKeyFromSecret(ctx, es.Namespace, secretName)
	if err != nil {
		// NotFound: credentials permanently gone — skip remote teardown so the
		// ExporterSet finalizer is not wedged. Any other error (timeout, RBAC,
		// conflict) must fail so Cleanup retries and does not orphan hosts.
		if apierrors.IsNotFound(err) {
			logger.Error(err, "SSH credentials secret missing, skipping remote teardown",
				"exporter", exporter.Name, "secret", secretName)
			return nil
		}
		return fmt.Errorf("read SSH key for cleanup: %w", err)
	}

	// Host (user/port) often lives on the ExporterSet, not the VTC — merge both
	// the same way Deploy does via the reconciler.
	mergedParams := mergeClassAndSetParameters(classParams, es.Spec.Parameters)
	host, err := ParseHost(mergedParams)
	if err != nil {
		logger.V(1).Info("parse host for cleanup failed, using defaults",
			"exporter", exporter.Name, "error", err)
		host = HostConfig{
			User: defaultSSHUser,
			Port: defaultSSHPort,
		}
	}

	conn, err := Connect(SSHConnectConfig{
		Host:       hostName,
		Port:       host.Port,
		User:       host.User,
		PrivateKey: privateKey,
	})
	if err != nil {
		logger.Error(err, "SSH connect for cleanup failed",
			"exporter", exporter.Name, "host", hostName)
		return nil
	}
	defer conn.Close() //nolint:errcheck

	p.teardownInstance(ctx, conn, exporter.Name)

	logger.Info("cleaned up remote exporter",
		"exporter", exporter.Name, "host", hostName)

	return nil
}

// deployInstance performs the actual SSH operations to set up the
// exporter on the remote host.
func (p *Provisioner) deployInstance(
	ctx context.Context,
	conn RemoteHost,
	es *virtualtargetv1alpha1.ExporterSet,
	mergedParameters map[string]any,
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
	caBundle string,
) error {
	logger := log.FromContext(ctx)
	name := exporter.Name

	if err := conn.MkdirAll(ctx, ExporterConfigDir); err != nil {
		return fmt.Errorf("mkdir %s: %w", ExporterConfigDir, err)
	}
	if err := conn.MkdirAll(ctx, QuadletDir); err != nil {
		return fmt.Errorf("mkdir %s: %w", QuadletDir, err)
	}

	exporterConfigContent, err := p.buildExporterConfig(ctx, es, exporter, caBundle, mergedParameters)
	if err != nil {
		return fmt.Errorf("build exporter config: %w", err)
	}

	configPath := filepath.Join(ExporterConfigDir, name+".yaml")
	changed, diff, err := conn.ReconcileFile(ctx, configPath, exporterConfigContent)
	if err != nil {
		return fmt.Errorf("reconcile exporter config: %w", err)
	}
	if changed {
		logger.Info("exporter config written", "path", configPath, "diff", diff)
	}

	rtCfg := ParseRuntimeConfig(mergedParameters)
	exporterImage, runtimeImage := p.resolveImages(images)

	quadletCfg := QuadletConfig{
		Name:          name,
		Namespace:     es.Namespace,
		ExporterImage: exporterImage,
		RuntimeImage:  runtimeImage,
		KVM:           rtCfg.KVM,
		ExtraDevices:  rtCfg.ExtraDevices,
		HostNetwork:   rtCfg.HostNetwork,
	}

	runtimeQuadlet, err := RuntimeContainerFile(quadletCfg)
	if err != nil {
		return fmt.Errorf("generate runtime quadlet: %w", err)
	}
	runtimePath := filepath.Join(QuadletDir, RuntimeContainerFileName(name))
	changed, diff, err = conn.ReconcileFile(ctx, runtimePath, runtimeQuadlet)
	if err != nil {
		return fmt.Errorf("reconcile runtime quadlet: %w", err)
	}
	if changed {
		logger.Info("runtime quadlet written", "path", runtimePath, "diff", diff)
	}

	exporterQuadlet, err := ExporterContainerFile(quadletCfg)
	if err != nil {
		return fmt.Errorf("generate exporter quadlet: %w", err)
	}
	exporterPath := filepath.Join(QuadletDir, ExporterContainerFileName(name))
	changed, diff, err = conn.ReconcileFile(ctx, exporterPath, exporterQuadlet)
	if err != nil {
		return fmt.Errorf("reconcile exporter quadlet: %w", err)
	}
	if changed {
		logger.Info("exporter quadlet written", "path", exporterPath, "diff", diff)
	}

	volumeName := PodmanVolumeName(name)
	if res, err := conn.RunCommand(ctx,
		fmt.Sprintf("podman volume inspect %s >/dev/null 2>&1 || podman volume create %s",
			volumeName, volumeName)); err != nil {
		return fmt.Errorf("create shared volume: %w", err)
	} else if res.ExitCode != 0 {
		return fmt.Errorf("create shared volume: exit %d: %s", res.ExitCode, res.Stderr)
	}

	// Stage jumpstarter-exec from the exporter image into the shared
	// volume so the runtime container can use it as its entrypoint.
	// This mirrors the in-cluster QEMU "copy-jumpstarter-exec" init
	// container; for quadlets we do it with a one-shot podman run.
	stageCmd := fmt.Sprintf(
		"podman run --rm -v %s:%s:z %s sh -c 'cp %s %s && chmod 755 %s'",
		volumeName, sharedMountPath,
		exporterImage,
		jmpExecSrcPath, jmpExecDstPath,
		jmpExecDstPath,
	)
	if res, err := conn.RunCommand(ctx, stageCmd); err != nil {
		return fmt.Errorf("stage jumpstarter-exec: %w", err)
	} else if res.ExitCode != 0 {
		return fmt.Errorf("stage jumpstarter-exec: exit %d: %s", res.ExitCode, res.Stderr)
	}
	logger.Info("jumpstarter-exec staged", "volume", volumeName, "src", jmpExecSrcPath)

	if res, err := conn.RunCommand(ctx, "systemctl daemon-reload"); err != nil {
		return fmt.Errorf("systemctl daemon-reload: %w", err)
	} else if res.ExitCode != 0 {
		return fmt.Errorf("systemctl daemon-reload: exit %d: %s", res.ExitCode, res.Stderr)
	}

	runtimeSvc := RuntimeServiceName(name)
	exporterSvc := ExporterServiceName(name)

	// Quadlet-generated units live under /run/systemd/generator/ and
	// cannot be "enabled" (systemd rejects that with "transient or
	// generated").  Use plain "start" instead.
	if res, err := conn.RunCommand(ctx,
		fmt.Sprintf("systemctl start %s %s", runtimeSvc, exporterSvc)); err != nil {
		return fmt.Errorf("start services: %w", err)
	} else if res.ExitCode != 0 {
		return fmt.Errorf("start services %s %s: exit %d: %s",
			runtimeSvc, exporterSvc, res.ExitCode, res.Stderr)
	}

	return nil
}

// teardownInstance stops and removes all remote resources for an
// exporter instance.
func (p *Provisioner) teardownInstance(
	ctx context.Context,
	conn RemoteHost,
	name string,
) {
	logger := log.FromContext(ctx)

	runtimeSvc := RuntimeServiceName(name)
	exporterSvc := ExporterServiceName(name)

	// Bound stop: QEMU under TCG can ignore SIGTERM for a long time and
	// wedge ExporterSet finalizers / e2e AfterAll. Force-kill after 60s,
	// then remove containers explicitly.
	if _, err := conn.RunCommand(ctx, fmt.Sprintf(
		"timeout 60 systemctl stop %s %s 2>/dev/null || "+
			"systemctl kill --kill-who=all %s %s 2>/dev/null || true; "+
			"podman rm -f %s-exporter %s-runtime 2>/dev/null || true",
		exporterSvc, runtimeSvc, exporterSvc, runtimeSvc, name, name,
	)); err != nil {
		logger.Error(err, "failed to stop services", "exporter", name)
	}

	for _, path := range []string{
		filepath.Join(QuadletDir, ExporterContainerFileName(name)),
		filepath.Join(QuadletDir, RuntimeContainerFileName(name)),
		filepath.Join(ExporterConfigDir, name+".yaml"),
	} {
		if err := conn.RemoveFile(ctx, path); err != nil {
			logger.Error(err, "failed to remove file", "path", path)
		}
	}

	volumeName := PodmanVolumeName(name)
	if _, err := conn.RunCommand(ctx,
		fmt.Sprintf("podman volume rm -f %s 2>/dev/null || true", volumeName)); err != nil {
		logger.Error(err, "failed to remove volume", "volume", volumeName)
	}

	if _, err := conn.RunCommand(ctx, "systemctl daemon-reload"); err != nil {
		logger.Error(err, "failed to reload systemd after cleanup")
	}
}

// readSSHKey reads the SSH private key from the VTC's
// credentialsSecretRef.
func (p *Provisioner) readSSHKey(
	ctx context.Context,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
) ([]byte, error) {
	if vtc.Spec.CredentialsSecretRef == nil {
		return nil, fmt.Errorf("VirtualTargetClass %s/%s has no credentialsSecretRef (required for SSH)",
			vtc.Namespace, vtc.Name)
	}
	return p.readSSHKeyFromSecret(ctx, vtc.Namespace, vtc.Spec.CredentialsSecretRef.Name)
}

// readSSHKeyFromSecret loads the ssh-privatekey field from a Secret.
func (p *Provisioner) readSSHKeyFromSecret(
	ctx context.Context,
	namespace, secretName string,
) ([]byte, error) {
	var secret corev1.Secret
	if err := p.Client.Get(ctx, client.ObjectKey{
		Name:      secretName,
		Namespace: namespace,
	}, &secret); err != nil {
		return nil, fmt.Errorf("get SSH credentials Secret %q: %w", secretName, err)
	}

	key, ok := secret.Data[sshPrivateKeyField]
	if !ok {
		return nil, fmt.Errorf("credentials Secret %q missing %q key",
			secretName, sshPrivateKeyField)
	}

	return key, nil
}

// annotateHost records host assignment (and credentials Secret) on the
// Exporter CR so Cleanup can tear down without the VirtualTargetClass.
func (p *Provisioner) annotateHost(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
	hostName, credentialsSecret string,
) error {
	if exporter.Annotations == nil {
		exporter.Annotations = make(map[string]string)
	}
	exporter.Annotations[AnnotationHost] = hostName
	if credentialsSecret != "" {
		exporter.Annotations[AnnotationCredentialsSecret] = credentialsSecret
	}
	return p.Client.Update(ctx, exporter)
}

// resolveImages returns the exporter and runtime images, applying
// overrides and version resolution.
func (p *Provisioner) resolveImages(
	images *virtualtargetv1alpha1.ImageOverrides,
) (exporterImage, runtimeImage string) {
	exporterImage = resolveImage(p.Version, DefaultExporterImage)
	runtimeImage = resolveImage(p.Version, DefaultQEMURuntimeImage)

	if images != nil {
		if images.Exporter != nil && images.Exporter.Image != "" {
			exporterImage = images.Exporter.Image
		}
		if images.Runtime != nil && images.Runtime.Image != "" {
			runtimeImage = images.Runtime.Image
		}
	}

	return exporterImage, runtimeImage
}

// resolveImage replaces :latest with the controller version tag.
func resolveImage(version, image string) string {
	if version == "" || version == "dev" || strings.Contains(version, "-g") {
		return image
	}
	v := strings.TrimPrefix(version, "v")
	if base, ok := strings.CutSuffix(image, ":latest"); ok {
		return base + ":" + v
	}
	return image
}

// buildExporterConfig generates the ExporterConfig YAML that will be
// written to the remote host.
func (p *Provisioner) buildExporterConfig(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
	caBundle string,
	mergedParameters map[string]any,
) (string, error) {
	token, err := p.readCredentialToken(ctx, exporter)
	if err != nil {
		return "", err
	}

	caBase64 := base64.StdEncoding.EncodeToString([]byte(caBundle))

	drivers := es.Spec.Template.Spec.Drivers
	drivers, err = enrichExporterExport(drivers, mergedParameters)
	if err != nil {
		return "", fmt.Errorf("enrich drivers: %w", err)
	}

	exportMap, err := buildExportMap(drivers)
	if err != nil {
		return "", fmt.Errorf("build export map: %w", err)
	}

	exitOnLeaseEnd := es.Spec.RecycleStrategy != virtualtargetv1alpha1.RecycleStrategyInPlaceReuse

	cfg := exporterConfig{
		APIVersion: "jumpstarter.dev/v1alpha1",
		Kind:       "ExporterConfig",
		Metadata: exporterConfigMetadata{
			Name:      exporter.Name,
			Namespace: exporter.Namespace,
		},
		Endpoint: exporter.Status.Endpoint,
		TLS: &exporterConfigTLS{
			CA: caBase64,
		},
		Token:          token,
		Export:         exportMap,
		ExitOnLeaseEnd: exitOnLeaseEnd,
	}

	cfgYAML, err := sigsyaml.Marshal(cfg)
	if err != nil {
		return "", fmt.Errorf("marshal ExporterConfig: %w", err)
	}

	return string(cfgYAML), nil
}

// readCredentialToken reads the JWT from the Exporter's credential
// Secret.
func (p *Provisioner) readCredentialToken(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (string, error) {
	var secret corev1.Secret
	if err := p.Client.Get(ctx, client.ObjectKey{
		Name:      exporter.Status.Credential.Name,
		Namespace: exporter.Namespace,
	}, &secret); err != nil {
		return "", fmt.Errorf("get credential Secret %q: %w",
			exporter.Status.Credential.Name, err)
	}

	token, ok := secret.Data["token"]
	if !ok {
		return "", fmt.Errorf("credential Secret %q missing 'token' key",
			exporter.Status.Credential.Name)
	}

	return string(token), nil
}

// mergeClassAndSetParameters overlays ExporterSet parameters on VirtualTargetClass
// parameters (shallow top-level merge). Host config for qemu-ssh typically lives
// on the ExporterSet.
func mergeClassAndSetParameters(
	classParams *apiextensionsv1.JSON,
	setParams *apiextensionsv1.JSON,
) map[string]any {
	merged := map[string]any{}
	if classParams != nil && classParams.Raw != nil {
		_ = json.Unmarshal(classParams.Raw, &merged)
	}
	if setParams != nil && setParams.Raw != nil {
		override := map[string]any{}
		if err := json.Unmarshal(setParams.Raw, &override); err == nil {
			maps.Copy(merged, override)
		}
	}
	return merged
}

// --- ExporterConfig types (mirrors exporterconfig.go in parent package) ---

type exporterConfig struct {
	APIVersion     string                          `json:"apiVersion"`
	Kind           string                          `json:"kind"`
	Metadata       exporterConfigMetadata          `json:"metadata"`
	Endpoint       string                          `json:"endpoint"`
	TLS            *exporterConfigTLS              `json:"tls,omitempty"`
	Token          string                          `json:"token"`
	Export         map[string]exporterConfigDriver `json:"export,omitempty"`
	ExitOnLeaseEnd bool                            `json:"exitOnLeaseEnd"`
}

type exporterConfigMetadata struct {
	Name      string `json:"name"`
	Namespace string `json:"namespace"`
}

type exporterConfigTLS struct {
	CA string `json:"ca"`
}

type exporterConfigDriver struct {
	Type     string                          `json:"type,omitempty"`
	Ref      string                          `json:"ref,omitempty"`
	Config   any                             `json:"config,omitempty"`
	Children map[string]exporterConfigDriver `json:"children,omitempty"`
}

// buildExportMap converts DriverConfigs to the export map (mirrors
// the parent package's buildExportMap).
func buildExportMap(
	drivers []virtualtargetv1alpha1.DriverConfig,
) (map[string]exporterConfigDriver, error) {
	exportMap := make(map[string]exporterConfigDriver, len(drivers))

	for _, d := range drivers {
		if _, exists := exportMap[d.Name]; exists {
			return nil, fmt.Errorf("duplicate driver key %q", d.Name)
		}

		if d.Ref != "" {
			exportMap[d.Name] = exporterConfigDriver{Ref: d.Ref}
			continue
		}

		var config any
		if d.Config != nil && d.Config.Raw != nil {
			if err := json.Unmarshal(d.Config.Raw, &config); err != nil {
				return nil, fmt.Errorf("unmarshal config for driver %q: %w", d.Name, err)
			}
		}

		exportMap[d.Name] = exporterConfigDriver{
			Type:   d.Type,
			Config: config,
		}
	}

	return exportMap, nil
}
