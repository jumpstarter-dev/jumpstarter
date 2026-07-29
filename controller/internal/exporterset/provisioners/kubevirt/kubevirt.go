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

// Package kubevirt implements the kubevirt.jumpstarter.dev provisioner
// for ExporterSets. It creates VirtualMachine CRs on a remote KubeVirt
// cluster and lightweight exporter-only Pods on the management cluster.
package kubevirt

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"strings"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	kubevirtv1 "kubevirt.io/api/core/v1"
	cdiv1beta1 "kubevirt.io/containerized-data-importer-api/pkg/apis/core/v1beta1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const (
	ProvisionerName = "kubevirt.jumpstarter.dev"

	DefaultExporterImage = "quay.io/jumpstarter-dev/jumpstarter:latest"

	jmpExecBinaryPath = "/jumpstarter/bin/jumpstarter-exec"
	configMountPath   = "/etc/jumpstarter/exporters"
	sharedVolumeName  = "shared"
	sharedMountPath   = "/shared"

	labelManagedBy  = "app.kubernetes.io/managed-by"
	labelExporter   = "jumpstarter.dev/exporter"
	labelProvenance = "jumpstarter.dev/provisioner"

	credentialsVolumeName = "kubevirt-credentials"
	credentialsMountPath  = "/etc/jumpstarter/kubevirt"
	kubeconfigFilename    = "kubeconfig"

	kubevirtDriverType  = "jumpstarter_driver_kubevirt.driver.Kubevirt"
	kubevirtPowerType   = "jumpstarter_driver_kubevirt.driver.KubevirtPower"
	kubevirtConsoleType = "jumpstarter_driver_kubevirt.driver.KubevirtConsole"
	kubevirtStorageType = "jumpstarter_driver_kubevirt.driver.KubevirtStorage"
	tcpDriverType       = "jumpstarter_driver_network.driver.TcpNetwork"
)

// Provisioner implements the kubevirt.jumpstarter.dev provisioner.
type Provisioner struct {
	Version       string
	Client        client.Client
	remoteClients *remoteClientCache
}

// New creates a new KubeVirt provisioner.
func New(version string, c client.Client) *Provisioner {
	return &Provisioner{
		Version:       version,
		Client:        c,
		remoteClients: newRemoteClientCache(),
	}
}

func (p *Provisioner) Name() string {
	return ProvisionerName
}

func (p *Provisioner) resolveImage(image string) string {
	if p.Version == "" || p.Version == "dev" || strings.Contains(p.Version, "-g") {
		return image
	}
	version := strings.TrimPrefix(p.Version, "v")
	if base, ok := strings.CutSuffix(image, ":latest"); ok {
		return base + ":" + version
	}
	return image
}

func (p *Provisioner) resolveImageSpec(spec *virtualtargetv1alpha1.ImageSpec, defaultImage string) (string, corev1.PullPolicy) {
	image := p.resolveImage(defaultImage)
	pullPolicy := corev1.PullIfNotPresent

	if spec != nil {
		if spec.Image != "" {
			image = spec.Image
		}
		if spec.ImagePullPolicy != "" {
			pullPolicy = spec.ImagePullPolicy
		}
	}

	return image, pullPolicy
}

// RenderPod creates a VirtualMachine on the remote KubeVirt cluster and
// returns a lightweight exporter-only Pod for the management cluster.
func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]any,
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	params, err := parseParams(mergedParameters)
	if err != nil {
		return nil, fmt.Errorf("parse kubevirt parameters: %w", err)
	}

	remoteClient, err := p.getRemoteClient(ctx, vtc)
	if err != nil {
		return nil, fmt.Errorf("get remote client: %w", err)
	}

	vm, err := p.buildVirtualMachine(exporter, params)
	if err != nil {
		return nil, fmt.Errorf("build VirtualMachine: %w", err)
	}

	if err := remoteClient.Create(ctx, vm); err != nil {
		if !apierrors.IsAlreadyExists(err) {
			return nil, fmt.Errorf("create VirtualMachine %s/%s on remote cluster: %w",
				vm.Namespace, vm.Name, err)
		}
		existing := &kubevirtv1.VirtualMachine{}
		if err := remoteClient.Get(ctx, client.ObjectKeyFromObject(vm), existing); err != nil {
			return nil, fmt.Errorf("get existing VirtualMachine %s/%s: %w",
				vm.Namespace, vm.Name, err)
		}
		if existing.Labels[labelManagedBy] != "jumpstarter" ||
			existing.Labels[labelProvenance] != ProvisionerName ||
			existing.Labels[labelExporter] != exporter.Name {
			return nil, fmt.Errorf(
				"VirtualMachine %s/%s already exists but is not managed by this provisioner "+
					"(labels: managed-by=%q, provisioner=%q, exporter=%q)",
				vm.Namespace, vm.Name,
				existing.Labels[labelManagedBy],
				existing.Labels[labelProvenance],
				existing.Labels[labelExporter],
			)
		}
		vm = existing
	}

	if err := p.createServices(ctx, remoteClient, vm, params, exporter); err != nil {
		return nil, fmt.Errorf("create services: %w", err)
	}

	pod := p.buildExporterPod(exporterSet, vtc, images, exporter)

	if vtc.Spec.Scheduling != nil {
		if vtc.Spec.Scheduling.NodeSelector != nil {
			pod.Spec.NodeSelector = maps.Clone(vtc.Spec.Scheduling.NodeSelector)
		}
		if vtc.Spec.Scheduling.Tolerations != nil {
			pod.Spec.Tolerations = append([]corev1.Toleration(nil), vtc.Spec.Scheduling.Tolerations...)
		}
	}

	return pod, nil
}

// buildVirtualMachine constructs a KubeVirt VirtualMachine CR.
// Created with RunStrategy Halted - the Python driver's power.on() starts it.
func (p *Provisioner) buildVirtualMachine(
	exporter *jumpstarterdevv1alpha1.Exporter,
	params *KubevirtParams,
) (*kubevirtv1.VirtualMachine, error) {
	runStrategy := kubevirtv1.RunStrategyHalted

	memory, err := resource.ParseQuantity(params.Memory)
	if err != nil {
		return nil, fmt.Errorf("parse memory %q: %w", params.Memory, err)
	}

	vm := &kubevirtv1.VirtualMachine{
		ObjectMeta: metav1.ObjectMeta{
			Name:      exporter.Name,
			Namespace: params.Namespace,
			Labels: map[string]string{
				labelManagedBy:  "jumpstarter",
				labelExporter:   exporter.Name,
				labelProvenance: ProvisionerName,
			},
		},
		Spec: kubevirtv1.VirtualMachineSpec{
			RunStrategy: &runStrategy,
			Template: &kubevirtv1.VirtualMachineInstanceTemplateSpec{
				Spec: kubevirtv1.VirtualMachineInstanceSpec{
					Domain: kubevirtv1.DomainSpec{
						CPU: &kubevirtv1.CPU{
							Cores: uint32(params.CPU), // #nosec G115 -- validated 1..MaxUint32 by parseParams
						},
						Memory: &kubevirtv1.Memory{
							Guest: &memory,
						},
						Devices: kubevirtv1.Devices{
							Disks: []kubevirtv1.Disk{
								{
									Name: "root",
									DiskDevice: kubevirtv1.DiskDevice{
										Disk: &kubevirtv1.DiskTarget{
											Bus: kubevirtv1.DiskBusVirtio,
										},
									},
								},
							},
							Interfaces: []kubevirtv1.Interface{
								{
									Name: "default",
									InterfaceBindingMethod: kubevirtv1.InterfaceBindingMethod{
										Masquerade: &kubevirtv1.InterfaceMasquerade{},
									},
								},
							},
						},
					},
					Networks: []kubevirtv1.Network{
						{
							Name: "default",
							NetworkSource: kubevirtv1.NetworkSource{
								Pod: &kubevirtv1.PodNetwork{},
							},
						},
					},
					Volumes: []kubevirtv1.Volume{},
				},
			},
		},
	}

	if params.Storage != nil && params.Storage.Type == StorageTypeDataVolume {
		dvName := exporter.Name + "-root"
		dvSource := &cdiv1beta1.DataVolumeSource{}
		if params.Storage.Source.HTTP != nil {
			dvSource.HTTP = &cdiv1beta1.DataVolumeSourceHTTP{
				URL: params.Storage.Source.HTTP.URL,
			}
		} else if params.Storage.Source.Registry != nil {
			url := "docker://" + params.Storage.Source.Registry.Image
			dvSource.Registry = &cdiv1beta1.DataVolumeSourceRegistry{
				URL: &url,
			}
		} else if params.Storage.Source.PVC != nil {
			dvSource.PVC = &cdiv1beta1.DataVolumeSourcePVC{
				Name:      params.Storage.Source.PVC.Name,
				Namespace: params.Storage.Source.PVC.Namespace,
			}
		}

		diskSize, err := resource.ParseQuantity(params.DiskSize)
		if err != nil {
			return nil, fmt.Errorf("parse diskSize %q: %w", params.DiskSize, err)
		}
		accessModes := make([]corev1.PersistentVolumeAccessMode, len(params.Storage.AccessModes))
		for i, m := range params.Storage.AccessModes {
			accessModes[i] = corev1.PersistentVolumeAccessMode(m)
		}

		dvSpec := cdiv1beta1.DataVolumeSpec{
			Source: dvSource,
			Storage: &cdiv1beta1.StorageSpec{
				AccessModes: accessModes,
				Resources: corev1.VolumeResourceRequirements{
					Requests: corev1.ResourceList{
						corev1.ResourceStorage: diskSize,
					},
				},
			},
		}
		if params.Storage.StorageClassName != "" {
			dvSpec.Storage.StorageClassName = &params.Storage.StorageClassName
		}

		vm.Spec.DataVolumeTemplates = []kubevirtv1.DataVolumeTemplateSpec{
			{
				ObjectMeta: metav1.ObjectMeta{
					Name: dvName,
				},
				Spec: dvSpec,
			},
		}
		vm.Spec.Template.Spec.Volumes = append(vm.Spec.Template.Spec.Volumes, kubevirtv1.Volume{
			Name: "root",
			VolumeSource: kubevirtv1.VolumeSource{
				DataVolume: &kubevirtv1.DataVolumeSource{
					Name: dvName,
				},
			},
		})
	} else {
		vm.Spec.Template.Spec.Volumes = append(vm.Spec.Template.Spec.Volumes, kubevirtv1.Volume{
			Name: "root",
			VolumeSource: kubevirtv1.VolumeSource{
				ContainerDisk: &kubevirtv1.ContainerDiskSource{
					Image: params.Image,
				},
			},
		})
	}

	vmiSpec := &vm.Spec.Template.Spec
	iface := &vmiSpec.Domain.Devices.Interfaces[0]
	if params.SSHAccess != "" {
		iface.Ports = append(iface.Ports, kubevirtv1.Port{
			Name:     "ssh",
			Port:     22,
			Protocol: "TCP",
		})
	}
	for name, port := range params.ForwardPorts {
		iface.Ports = append(iface.Ports, kubevirtv1.Port{
			Name:     name,
			Port:     int32(port), // #nosec G115 -- validated 1..65535 by parseParams
			Protocol: "TCP",
		})
	}

	if params.CloudInit != "" {
		vmiSpec.Volumes = append(vmiSpec.Volumes, kubevirtv1.Volume{
			Name: "cloudinit",
			VolumeSource: kubevirtv1.VolumeSource{
				CloudInitNoCloud: &kubevirtv1.CloudInitNoCloudSource{
					UserData: params.CloudInit,
				},
			},
		})
		vmiSpec.Domain.Devices.Disks = append(vmiSpec.Domain.Devices.Disks, kubevirtv1.Disk{
			Name: "cloudinit",
			DiskDevice: kubevirtv1.DiskDevice{
				Disk: &kubevirtv1.DiskTarget{
					Bus: kubevirtv1.DiskBusVirtio,
				},
			},
		})
	}

	return vm, nil
}

// createServices creates Kubernetes Services on the remote cluster to expose
// SSH and forwardPorts. Services use ownerReferences to the VM for lifecycle
// management and are idempotent (AlreadyExists is ignored).
func (p *Provisioner) createServices(
	ctx context.Context,
	remoteClient client.Client,
	vm *kubevirtv1.VirtualMachine,
	params *KubevirtParams,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	if params.SSHAccess == "" && len(params.ForwardPorts) == 0 {
		return nil
	}

	svcType := corev1.ServiceTypeNodePort
	if params.SSHAccess == "LoadBalancer" {
		svcType = corev1.ServiceTypeLoadBalancer
	}

	ownerRef := metav1.OwnerReference{
		APIVersion: kubevirtv1.GroupVersion.String(),
		Kind:       "VirtualMachine",
		Name:       vm.Name,
		UID:        vm.UID,
	}

	if params.SSHAccess != "" {
		if err := p.createService(ctx, remoteClient, params.Namespace, ownerRef, exporter.Name,
			"ssh", svcType, 22000, 22); err != nil {
			return err
		}
	}

	for name, port := range params.ForwardPorts {
		if err := p.createService(ctx, remoteClient, params.Namespace, ownerRef, exporter.Name,
			name, svcType, int32(port), int32(port)); err != nil { // #nosec G115 -- validated 1..65535
			return err
		}
	}

	return nil
}

func (p *Provisioner) createService(
	ctx context.Context,
	remoteClient client.Client,
	namespace string,
	ownerRef metav1.OwnerReference,
	exporterName, portName string,
	svcType corev1.ServiceType,
	svcPort, targetPort int32,
) error {
	svc := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:            exporterName + "-" + portName,
			Namespace:       namespace,
			OwnerReferences: []metav1.OwnerReference{ownerRef},
			Labels: map[string]string{
				labelManagedBy:  "jumpstarter",
				labelExporter:   exporterName,
				labelProvenance: ProvisionerName,
			},
		},
		Spec: corev1.ServiceSpec{
			Type: svcType,
			Selector: map[string]string{
				"vm.kubevirt.io/name": exporterName,
			},
			Ports: []corev1.ServicePort{
				{
					Name:       portName,
					Port:       svcPort,
					TargetPort: intstr.FromInt32(targetPort),
					Protocol:   corev1.ProtocolTCP,
				},
			},
		},
	}

	if err := remoteClient.Create(ctx, svc); err != nil {
		if !apierrors.IsAlreadyExists(err) {
			return fmt.Errorf("create Service %s/%s: %w", namespace, svc.Name, err)
		}
	}
	return nil
}

// buildExporterPod creates a lightweight Pod that runs only the Jumpstarter
// exporter sidecar — no target-runtime container. The VM runs on the remote
// cluster; this Pod just hosts the exporter process.
func (p *Provisioner) buildExporterPod(
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
) *corev1.Pod {
	restartAlways := corev1.ContainerRestartPolicyAlways

	var exporterSpec *virtualtargetv1alpha1.ImageSpec
	if images != nil {
		exporterSpec = images.Exporter
	}
	exporterImage, exporterPullPolicy := p.resolveImageSpec(exporterSpec, DefaultExporterImage)

	podMeta := metav1.ObjectMeta{
		Namespace:   exporterSet.Namespace,
		Labels:      maps.Clone(exporterSet.Spec.Template.Metadata.Labels),
		Annotations: maps.Clone(exporterSet.Spec.Template.Metadata.Annotations),
	}
	if exporter != nil {
		podMeta.Name = exporter.Name
	} else {
		podMeta.GenerateName = fmt.Sprintf("%s-", exporterSet.Name)
	}

	sizeLimit := resource.MustParse("10Mi")

	return &corev1.Pod{
		ObjectMeta: podMeta,
		Spec: corev1.PodSpec{
			InitContainers: []corev1.Container{
				{
					Name:            "copy-jumpstarter-exec",
					Image:           exporterImage,
					ImagePullPolicy: exporterPullPolicy,
					Command: []string{
						"cp",
						jmpExecBinaryPath,
						sharedMountPath + "/jumpstarter-exec",
					},
					VolumeMounts: []corev1.VolumeMount{
						{Name: sharedVolumeName, MountPath: sharedMountPath},
					},
				},
				{
					Name:            "exporter",
					Image:           exporterImage,
					ImagePullPolicy: exporterPullPolicy,
					RestartPolicy:   &restartAlways,
					Command:         []string{"jmp", "run", "--exporter-config", configMountPath + "/config.yaml"},
					VolumeMounts: []corev1.VolumeMount{
						{Name: sharedVolumeName, MountPath: sharedMountPath},
						{Name: credentialsVolumeName, MountPath: credentialsMountPath, ReadOnly: true},
					},
				},
			},
			Containers: []corev1.Container{
				{
					Name:            "keepalive",
					Image:           exporterImage,
					ImagePullPolicy: exporterPullPolicy,
					Command:         []string{"sleep", "infinity"},
				},
			},
			Volumes: []corev1.Volume{
				{
					Name: sharedVolumeName,
					VolumeSource: corev1.VolumeSource{
						EmptyDir: &corev1.EmptyDirVolumeSource{
							SizeLimit: &sizeLimit,
						},
					},
				},
				{
					Name: credentialsVolumeName,
					VolumeSource: corev1.VolumeSource{
						Secret: &corev1.SecretVolumeSource{
							SecretName: vtc.Spec.CredentialsSecretRef.Name,
						},
					},
				},
			},
		},
	}
}

// EnrichExporterExport injects KubeVirt-specific driver configuration
// so the Python driver knows how to reach the remote VM.
func (p *Provisioner) EnrichExporterExport(
	ctx context.Context,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]any,
	exporter *jumpstarterdevv1alpha1.Exporter,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	params, err := parseParams(mergedParameters)
	if err != nil {
		return nil, fmt.Errorf("parse kubevirt parameters: %w", err)
	}

	vmName := exporter.Name

	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers)+6)

	hasKubevirt := false
	for _, d := range drivers {
		if d.Type == kubevirtDriverType {
			hasKubevirt = true
		}
		result = append(result, d)
	}

	kubeconfigPath := credentialsMountPath + "/" + kubeconfigFilename

	if !hasKubevirt {
		kubevirtCfg := mustJSON(map[string]any{
			"namespace":  params.Namespace,
			"vm_name":    vmName,
			"kubeconfig": kubeconfigPath,
		})

		result = append(result,
			virtualtargetv1alpha1.DriverConfig{Name: "power", Type: kubevirtPowerType, Config: kubevirtCfg},
			virtualtargetv1alpha1.DriverConfig{Name: "console", Type: kubevirtConsoleType, Config: kubevirtCfg},
			virtualtargetv1alpha1.DriverConfig{Name: "storage", Type: kubevirtStorageType, Config: kubevirtCfg},
		)

		if params.SSHAccess != "" {
			sshHost, sshPort := p.resolveServiceAddress(ctx, vtc, params, exporter.Name+"-ssh", 22000)
			if sshHost != "" {
				result = append(result, virtualtargetv1alpha1.DriverConfig{
					Name: "ssh",
					Type: tcpDriverType,
					Config: mustJSON(map[string]any{
						"host": sshHost,
						"port": sshPort,
					}),
				})
			}
		}

		for name, port := range params.ForwardPorts {
			if findDriverByName(drivers, name) != nil {
				continue
			}
			host, resolvedPort := p.resolveServiceAddress(ctx, vtc, params, exporter.Name+"-"+name, int32(port))
			if host != "" {
				result = append(result, virtualtargetv1alpha1.DriverConfig{
					Name:   name,
					Type:   tcpDriverType,
					Config: mustJSON(map[string]any{"host": host, "port": resolvedPort}),
				})
			}
		}
	}

	return result, nil
}

// resolveServiceAddress fetches a Service from the remote cluster and returns
// the best reachable address (LoadBalancer ingress IP, then ClusterIP).
func (p *Provisioner) resolveServiceAddress(
	ctx context.Context,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	params *KubevirtParams,
	svcName string,
	defaultPort int32,
) (string, int32) {
	if vtc == nil {
		return "", 0
	}

	remoteClient, err := p.getRemoteClient(ctx, vtc)
	if err != nil {
		return "", 0
	}

	svc := &corev1.Service{}
	key := client.ObjectKey{Name: svcName, Namespace: params.Namespace}
	if err := remoteClient.Get(ctx, key, svc); err != nil {
		return "", 0
	}

	port := defaultPort
	if len(svc.Spec.Ports) > 0 {
		if svc.Spec.Type == corev1.ServiceTypeNodePort && svc.Spec.Ports[0].NodePort > 0 {
			port = svc.Spec.Ports[0].NodePort
		} else {
			port = svc.Spec.Ports[0].Port
		}
	}

	if svc.Spec.Type == corev1.ServiceTypeLoadBalancer {
		for _, ing := range svc.Status.LoadBalancer.Ingress {
			if ing.IP != "" {
				return ing.IP, port
			}
			if ing.Hostname != "" {
				return ing.Hostname, port
			}
		}
	}

	if svc.Spec.ClusterIP != "" && svc.Spec.ClusterIP != "None" {
		return svc.Spec.ClusterIP, port
	}

	return "", 0
}

// Cleanup deletes the VirtualMachine and any SSH Service on the remote cluster
// when an exporter instance is being removed.
func (p *Provisioner) Cleanup(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{}
	vtcKey := client.ObjectKey{
		Name:      exporterSet.Spec.VirtualTargetClassName,
		Namespace: exporterSet.Namespace,
	}
	if err := p.Client.Get(ctx, vtcKey, vtc); err != nil {
		return fmt.Errorf("read VirtualTargetClass %s: %w", vtcKey, err)
	}

	remoteClient, err := p.getRemoteClient(ctx, vtc)
	if err != nil {
		return fmt.Errorf("get remote client for cleanup: %w", err)
	}

	params, err := parseParams(mergeVTCAndESParams(vtc, exporterSet))
	if err != nil {
		return fmt.Errorf("parse parameters for cleanup: %w", err)
	}

	vm := &kubevirtv1.VirtualMachine{
		ObjectMeta: metav1.ObjectMeta{
			Name:      exporter.Name,
			Namespace: params.Namespace,
		},
	}
	if err := remoteClient.Delete(ctx, vm); client.IgnoreNotFound(err) != nil {
		return fmt.Errorf("delete VirtualMachine %s/%s: %w", params.Namespace, exporter.Name, err)
	}

	if params.SSHAccess != "" {
		svc := &corev1.Service{
			ObjectMeta: metav1.ObjectMeta{
				Name:      exporter.Name + "-ssh",
				Namespace: params.Namespace,
			},
		}
		if err := remoteClient.Delete(ctx, svc); client.IgnoreNotFound(err) != nil {
			return fmt.Errorf("delete SSH Service %s/%s: %w", params.Namespace, svc.Name, err)
		}
	}

	for name := range params.ForwardPorts {
		svc := &corev1.Service{
			ObjectMeta: metav1.ObjectMeta{
				Name:      exporter.Name + "-" + name,
				Namespace: params.Namespace,
			},
		}
		if err := remoteClient.Delete(ctx, svc); client.IgnoreNotFound(err) != nil {
			return fmt.Errorf("delete forwarded port Service %s/%s: %w", params.Namespace, svc.Name, err)
		}
	}

	return nil
}

// mergeVTCAndESParams does a shallow merge of VTC and ExporterSet parameters,
// mirroring what the reconciler does before calling Cleanup.
func mergeVTCAndESParams(vtc *virtualtargetv1alpha1.VirtualTargetClass, es *virtualtargetv1alpha1.ExporterSet) map[string]any {
	merged := make(map[string]any)
	if vtc.Spec.Parameters != nil && vtc.Spec.Parameters.Raw != nil {
		_ = json.Unmarshal(vtc.Spec.Parameters.Raw, &merged)
	}
	if es.Spec.Parameters != nil && es.Spec.Parameters.Raw != nil {
		var esParams map[string]any
		if err := json.Unmarshal(es.Spec.Parameters.Raw, &esParams); err == nil {
			maps.Copy(merged, esParams)
		}
	}
	return merged
}

func findDriverByName(drivers []virtualtargetv1alpha1.DriverConfig, name string) *virtualtargetv1alpha1.DriverConfig {
	for i := range drivers {
		if drivers[i].Name == name {
			return &drivers[i]
		}
	}
	return nil
}

func mustJSON(v any) *apiextensionsv1.JSON {
	raw, _ := json.Marshal(v)
	return &apiextensionsv1.JSON{Raw: raw}
}
