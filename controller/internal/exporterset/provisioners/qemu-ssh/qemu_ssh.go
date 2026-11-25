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
	"path/filepath"
	"strings"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	sigsyaml "sigs.k8s.io/yaml"

	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// Compile-time check: *Provisioner must implement the Deployer interface.
var _ exporterset.Deployer = (*Provisioner)(nil)

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
// exporterset.Deployer interface (Deploy, DeployedAt).
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
//  3. Connect via SSH, so wrong details fail before anything is recorded
//  4. Record the SSH connection on the Exporter CR, before any remote
//     containers exist, so Cleanup can always reconnect and tear them down
//  5. Write exporter config, quadlet files
//  6. Create shared volume, reload systemd, start containers
//  7. Mark the Exporter CR deployed
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

	// Reuse a connection recorded by an earlier attempt. Deploy is retried until
	// the deployed-at marker is set, which includes a partial deploy that may have
	// left resources on that host. Re-deriving the host from parameters that changed
	// since would overwrite the record and strand those resources, because Cleanup
	// can only reach the recorded host. Reusing keeps Deploy and Cleanup on the same
	// host; only the first attempt derives and records a connection.
	connCfg, present, err := connectionFromExporter(exporter)
	if err != nil {
		return fmt.Errorf("recorded connection for %s is unusable: %w", exporter.Name, err)
	}

	var privateKey []byte
	if present {
		privateKey, err = p.readSSHKeyFromSecret(ctx, exporter.Namespace, connCfg.Secret)
	} else {
		var host HostConfig
		host, err = ParseHost(mergedParameters)
		if err != nil {
			return fmt.Errorf("parse host: %w", err)
		}
		// readSSHKey validates credentialsSecretRef, so reading it before building
		// connCfg keeps the .Name access below safe.
		privateKey, err = p.readSSHKey(ctx, vtc)
		if err == nil {
			connCfg = ConnectionConfig{
				HostConfig: host,
				Secret:     vtc.Spec.CredentialsSecretRef.Name,
			}
		}
	}
	if err != nil {
		return err
	}

	logger.Info("deploying exporter to host",
		"exporter", exporter.Name,
		"host", connCfg.Host,
		"reusedRecord", present,
	)

	// Connect before recording: if the details are wrong the dial fails here and
	// nothing is written, so a later Cleanup will not try to reach a host that
	// was never deployed to (which would raise a false orphan warning).
	conn, err := Connect(connCfg, privateKey)
	if err != nil {
		return fmt.Errorf("SSH connect to %s: %w", connCfg.Host, err)
	}
	defer conn.Close() //nolint:errcheck

	// Record the connection before creating any remote containers, so Cleanup can
	// always reconnect and tear them down even if the deploy below (or marking it
	// complete) fails partway. A reused record already points at that host.
	if !present {
		if err := p.recordConnection(ctx, exporter, connCfg); err != nil {
			return fmt.Errorf("record connection for %s: %w", exporter.Name, err)
		}
	}

	if err := p.deployInstance(ctx, conn, es, mergedParameters, images, exporter, caBundle); err != nil {
		return err
	}

	// Mark deployment complete. DeployedAt gates on this annotation (not the
	// host assignment), so a failure here leaves the exporter retryable:
	// deployInstance is idempotent and the host is already recorded.
	if err := p.markDeployed(ctx, exporter); err != nil {
		return fmt.Errorf("mark exporter %s deployed: %w", exporter.Name, err)
	}

	return nil
}

// DeployedAt reports whether Deploy previously ran to completion and, if so,
// when. The deployed-at annotation doubles as the deployment marker: its
// presence means deployment completed. The connection annotation alone is set
// before containers are created (so Cleanup can find them) and does not count —
// a partial deploy is retried.
func (p *Provisioner) DeployedAt(
	exporter *jumpstarterdevv1alpha1.Exporter,
) (at time.Time, deployed bool) {
	if exporter.Annotations == nil {
		return time.Time{}, false
	}
	raw, ok := exporter.Annotations[AnnotationDeployedAt]
	if !ok {
		return time.Time{}, false
	}
	t, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return time.Time{}, true
	}
	return t, true
}

// Cleanup tears down the remote containers via SSH, using the connection
// recorded on the Exporter at Deploy time. Because the connection (host, port,
// user, Secret) is read back verbatim, Cleanup needs neither the
// VirtualTargetClass nor the ExporterSet parameters, so the ExporterSet and its
// class can be deleted in any order.
//
// When the recorded Secret is gone or the host is unreachable, Cleanup cannot
// reach the remote resources. It returns an OrphanedResourcesError so the
// reconciler warns about (and does not wedge the finalizer on) the leftover
// containers, volume, quadlet files, and exporter config. Transient Secret-read
// errors (timeout, RBAC) still fail so Cleanup retries rather than orphaning on
// a blip.
func (p *Provisioner) Cleanup(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	logger := log.FromContext(ctx)

	connCfg, present, parseErr := connectionFromExporter(exporter)
	if !present {
		logger.V(1).Info("no connection recorded on exporter, skipping cleanup",
			"exporter", exporter.Name)
		return nil
	}
	if parseErr != nil {
		return orphanedResources(exporter.Name, connCfg.Host,
			fmt.Sprintf("recorded connection unusable: %v", parseErr))
	}

	privateKey, err := p.readSSHKeyFromSecret(ctx, es.Namespace, connCfg.Secret)
	if err != nil {
		// NotFound: credentials permanently gone — skip remote teardown so the
		// ExporterSet finalizer is not wedged. Any other error (timeout, RBAC,
		// conflict) must fail so Cleanup retries and does not orphan hosts.
		if apierrors.IsNotFound(err) {
			return orphanedResources(exporter.Name, connCfg.Host,
				fmt.Sprintf("SSH credentials Secret %q not found", connCfg.Secret))
		}
		return fmt.Errorf("read SSH key for cleanup: %w", err)
	}

	conn, err := Connect(connCfg, privateKey)
	if err != nil {
		return orphanedResources(exporter.Name, connCfg.Host,
			fmt.Sprintf("SSH connect failed: %v", err))
	}
	defer conn.Close() //nolint:errcheck

	p.teardownInstance(ctx, conn, exporter.Name)

	logger.Info("cleaned up remote exporter",
		"exporter", exporter.Name, "host", connCfg.Host)

	return nil
}

// orphanedResources reports that remote teardown was skipped, naming every
// resource that may still exist on the host so it can be removed by hand. The
// reconciler turns the returned error into a Warning Event and then clears the
// finalizer, rather than retrying against an unreachable host.
func orphanedResources(exporterName, host, reason string) error {
	volume := PodmanVolumeName(exporterName)
	return &exporterset.OrphanedResourcesError{
		Message: fmt.Sprintf(
			"remote teardown of exporter %s on host %s skipped (%s); "+
				"containers %s-exporter and %s-runtime, shared volume %s, "+
				"quadlet files under %s, and exporter config %s may remain and need manual removal on the host",
			exporterName, host, reason,
			exporterName, exporterName, volume,
			QuadletDir, filepath.Join(ExporterConfigDir, exporterName+".yaml"),
		),
	}
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

	// Stop the services first. An explicit `systemctl stop` suppresses the
	// runtime unit's Restart=always, and TimeoutStopSec (set in the quadlet)
	// bounds it so a SIGTERM-ignoring QEMU under TCG can't wedge the
	// finalizer. The outer `timeout` caps the ssh command itself. We do NOT
	// `podman rm -f` yet: removing a container while its unit still exists
	// would race the restart policy and resurrect it.
	if _, err := conn.RunCommand(ctx, fmt.Sprintf(
		"timeout 150 systemctl stop %s %s 2>/dev/null || true",
		exporterSvc, runtimeSvc,
	)); err != nil {
		logger.Error(err, "failed to stop services", "exporter", name)
	}

	// Remove the quadlet unit files and reload so the generated units are
	// gone and cannot restart the containers.
	for _, path := range []string{
		filepath.Join(QuadletDir, ExporterContainerFileName(name)),
		filepath.Join(QuadletDir, RuntimeContainerFileName(name)),
		filepath.Join(ExporterConfigDir, name+".yaml"),
	} {
		if err := conn.RemoveFile(ctx, path); err != nil {
			logger.Error(err, "failed to remove file", "path", path)
		}
	}
	if _, err := conn.RunCommand(ctx, "systemctl daemon-reload"); err != nil {
		logger.Error(err, "failed to reload systemd after cleanup")
	}

	// Units are gone now, so force-removing any stragglers is race-free.
	if _, err := conn.RunCommand(ctx,
		fmt.Sprintf("podman rm -f %s-exporter %s-runtime 2>/dev/null || true",
			name, name)); err != nil {
		logger.Error(err, "failed to remove containers", "exporter", name)
	}

	volumeName := PodmanVolumeName(name)
	if _, err := conn.RunCommand(ctx,
		fmt.Sprintf("podman volume rm -f %s 2>/dev/null || true", volumeName)); err != nil {
		logger.Error(err, "failed to remove volume", "volume", volumeName)
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

// recordConnection persists the SSH connection (host, port, user, credentials
// Secret) on the Exporter CR before any remote containers are created, so
// Cleanup can always reconnect and tear them down even if deployment later fails
// partway.
//
// It patches rather than updates: Deploy calls this and markDeployed around a
// long-running deployInstance, during which another controller may update the
// Exporter (status, LastSeen). A JSON merge patch touches only this annotation,
// so it neither conflicts on a stale resourceVersion nor clobbers concurrent
// changes.
func (p *Provisioner) recordConnection(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
	connCfg ConnectionConfig,
) error {
	raw, err := json.Marshal(connCfg)
	if err != nil {
		return fmt.Errorf("marshal connection: %w", err)
	}
	patch := client.MergeFrom(exporter.DeepCopy())
	if exporter.Annotations == nil {
		exporter.Annotations = make(map[string]string)
	}
	exporter.Annotations[AnnotationConnection] = string(raw)
	return p.Client.Patch(ctx, exporter, patch)
}

// markDeployed records that remote deployment completed. DeployedAt gates on
// this annotation, so an incomplete deploy is retried. It patches for the same
// conflict-avoidance reason as recordConnection.
func (p *Provisioner) markDeployed(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	patch := client.MergeFrom(exporter.DeepCopy())
	if exporter.Annotations == nil {
		exporter.Annotations = make(map[string]string)
	}
	exporter.Annotations[AnnotationDeployedAt] = time.Now().UTC().Format(time.RFC3339)
	return p.Client.Patch(ctx, exporter, patch)
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
