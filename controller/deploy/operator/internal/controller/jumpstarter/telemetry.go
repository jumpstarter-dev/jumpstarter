/*
Copyright 2026 by the Jumpstarter Authors

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
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

const (
	telemetryPort         = 9093
	telemetryCertSuffix   = "-telemetry-tls"
	telemetryServiceName  = "jumpstarter-telemetry"
	telemetryComponentApp = "jumpstarter-telemetry"
)

// reconcileTelemetry reconciles telemetry Deployment and Service.
// Certificate reconciliation is handled in certificates.go alongside other certs.
// When telemetry is disabled, existing telemetry resources are cleaned up.
func (r *JumpstarterReconciler) reconcileTelemetry(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	if jumpstarter.Spec.Telemetry == nil || !jumpstarter.Spec.Telemetry.Enabled {
		return r.cleanupTelemetry(ctx, jumpstarter)
	}

	if err := r.reconcileTelemetryDeployment(ctx, jumpstarter); err != nil {
		return fmt.Errorf("failed to reconcile telemetry deployment: %w", err)
	}

	if err := r.reconcileTelemetryService(ctx, jumpstarter); err != nil {
		return fmt.Errorf("failed to reconcile telemetry service: %w", err)
	}

	return nil
}

// reconcileTelemetryDeployment creates or updates the telemetry Deployment.
func (r *JumpstarterReconciler) reconcileTelemetryDeployment(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)
	desiredDeployment := r.createTelemetryDeployment(jumpstarter)

	existingDeployment := &appsv1.Deployment{}
	existingDeployment.Name = desiredDeployment.Name
	existingDeployment.Namespace = desiredDeployment.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingDeployment, func() error {
		if existingDeployment.CreationTimestamp.IsZero() {
			existingDeployment.Labels = desiredDeployment.Labels
			existingDeployment.Annotations = desiredDeployment.Annotations
			existingDeployment.Spec = desiredDeployment.Spec
			return controllerutil.SetControllerReference(jumpstarter, existingDeployment, r.Scheme)
		}

		desiredDeployment.Spec.Template.Spec.DeprecatedServiceAccount = existingDeployment.Spec.Template.Spec.DeprecatedServiceAccount
		desiredDeployment.Spec.Template.Spec.SchedulerName = existingDeployment.Spec.Template.Spec.SchedulerName

		if !deploymentNeedsUpdate(existingDeployment, desiredDeployment) {
			log.V(1).Info("Telemetry deployment is up to date", "name", existingDeployment.Name)
			return nil
		}

		diff, diffErr := generateDiff(existingDeployment, desiredDeployment)
		if diffErr != nil {
			log.V(1).Info("Failed to generate deployment diff", "error", diffErr)
		} else if diff != "" {
			fmt.Printf("\n=== Telemetry deployment differences detected ===\n")
			fmt.Printf("Name: %s\n", existingDeployment.Name)
			fmt.Printf("Namespace: %s\n", existingDeployment.Namespace)
			fmt.Printf("\n%s\n", diff)
			fmt.Printf("==================================================\n\n")
		}

		existingDeployment.Labels = desiredDeployment.Labels
		existingDeployment.Annotations = desiredDeployment.Annotations
		existingDeployment.Spec.Replicas = desiredDeployment.Spec.Replicas
		existingDeployment.Spec.Selector = desiredDeployment.Spec.Selector
		existingDeployment.Spec.Template = desiredDeployment.Spec.Template
		return controllerutil.SetControllerReference(jumpstarter, existingDeployment, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile telemetry deployment",
			"name", desiredDeployment.Name, "namespace", desiredDeployment.Namespace)
		return err
	}

	log.Info("Telemetry deployment reconciled",
		"name", existingDeployment.Name, "namespace", existingDeployment.Namespace, "operation", op)

	switch op {
	case controllerutil.OperationResultCreated:
		r.emitEventf(jumpstarter, corev1.EventTypeNormal, "TelemetryDeploymentCreated",
			"Telemetry deployment created: name=%s namespace=%s",
			existingDeployment.Name, existingDeployment.Namespace)
	case controllerutil.OperationResultUpdated:
		r.emitEventf(jumpstarter, corev1.EventTypeNormal, "TelemetryDeploymentUpdated",
			"Telemetry deployment updated: name=%s namespace=%s",
			existingDeployment.Name, existingDeployment.Namespace)
	}

	return nil
}

// reconcileTelemetryService creates or updates the telemetry ClusterIP Service.
func (r *JumpstarterReconciler) reconcileTelemetryService(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	labels := telemetryLabels(jumpstarter)
	desiredService := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      telemetryServiceName,
			Namespace: jumpstarter.Namespace,
			Labels:    labels,
		},
		Spec: corev1.ServiceSpec{
			Type:     corev1.ServiceTypeClusterIP,
			Selector: labels,
			Ports: []corev1.ServicePort{
				{
					Name:       "grpc",
					Port:       int32(telemetryPort),
					TargetPort: intstr.FromInt(telemetryPort),
					Protocol:   corev1.ProtocolTCP,
				},
			},
		},
	}

	existingService := &corev1.Service{}
	existingService.Name = desiredService.Name
	existingService.Namespace = desiredService.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingService, func() error {
		if existingService.CreationTimestamp.IsZero() {
			existingService.Labels = desiredService.Labels
			existingService.Spec.Type = desiredService.Spec.Type
			existingService.Spec.Selector = desiredService.Spec.Selector
			existingService.Spec.Ports = desiredService.Spec.Ports
			return controllerutil.SetControllerReference(jumpstarter, existingService, r.Scheme)
		}

		existingService.Labels = desiredService.Labels
		existingService.Spec.Selector = desiredService.Spec.Selector
		existingService.Spec.Ports = desiredService.Spec.Ports
		return controllerutil.SetControllerReference(jumpstarter, existingService, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile telemetry service", "name", desiredService.Name)
		return err
	}

	log.Info("Telemetry service reconciled",
		"name", existingService.Name, "namespace", existingService.Namespace, "operation", op)

	return nil
}

// createTelemetryDeployment builds the desired Deployment for the telemetry service.
func (r *JumpstarterReconciler) createTelemetryDeployment(jumpstarter *operatorv1alpha1.Jumpstarter) *appsv1.Deployment {
	t := jumpstarter.Spec.Telemetry
	labels := telemetryLabels(jumpstarter)

	replicas := int32(1)
	if t.Replicas != nil {
		replicas = *t.Replicas
	}

	telemetryEndpoint := telemetryEndpointFor(jumpstarter.Namespace)

	envVars := []corev1.EnvVar{
		{
			Name:  "GRPC_TELEMETRY_ENDPOINT",
			Value: telemetryEndpoint,
		},
		{
			Name: "CONTROLLER_KEY",
			ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{
						Name: "jumpstarter-controller-secret",
					},
					Key: "key",
				},
			},
		},
	}

	var volumeMounts []corev1.VolumeMount
	var volumes []corev1.Volume

	// Add TLS certificate mount when cert-manager is enabled
	var tlsSecretName string
	if jumpstarter.Spec.CertManager.Enabled {
		tlsSecretName = GetTelemetryCertSecretName(jumpstarter)
	}

	if tlsSecretName != "" {
		envVars = append(envVars,
			corev1.EnvVar{Name: "EXTERNAL_CERT_PEM", Value: "/tls/tls.crt"},
			corev1.EnvVar{Name: "EXTERNAL_KEY_PEM", Value: "/tls/tls.key"},
		)
		volumeMounts = append(volumeMounts, corev1.VolumeMount{
			Name:      "tls-certs",
			MountPath: "/tls",
			ReadOnly:  true,
		})
		defaultMode := int32(420)
		volumes = append(volumes, corev1.Volume{
			Name: "tls-certs",
			VolumeSource: corev1.VolumeSource{
				Secret: &corev1.SecretVolumeSource{
					SecretName:  tlsSecretName,
					DefaultMode: &defaultMode,
				},
			},
		})
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-telemetry", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas:                &replicas,
			ProgressDeadlineSeconds: ptr.To(int32(600)),
			RevisionHistoryLimit:    ptr.To(int32(10)),
			Strategy: appsv1.DeploymentStrategy{
				Type: appsv1.RollingUpdateDeploymentStrategyType,
				RollingUpdate: &appsv1.RollingUpdateDeployment{
					MaxSurge:       &intstr.IntOrString{Type: intstr.String, StrVal: "25%"},
					MaxUnavailable: &intstr.IntOrString{Type: intstr.String, StrVal: "25%"},
				},
			},
			Selector: &metav1.LabelSelector{
				MatchLabels: labels,
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: labels,
				},
				Spec: corev1.PodSpec{
					RestartPolicy:                 corev1.RestartPolicyAlways,
					DNSPolicy:                     corev1.DNSClusterFirst,
					TerminationGracePeriodSeconds: ptr.To(int64(30)),
					Containers: []corev1.Container{
						{
							Name:            "telemetry",
							Image:           t.Image,
							ImagePullPolicy: t.ImagePullPolicy,
							Command:         []string{"/telemetry"},
							Args: []string{
								fmt.Sprintf("--grpc-bind=:%d", telemetryPort),
							},
							Env:          envVars,
							VolumeMounts: volumeMounts,
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: int32(telemetryPort),
									Name:          "grpc",
									Protocol:      corev1.ProtocolTCP,
								},
							},
							LivenessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									TCPSocket: &corev1.TCPSocketAction{
										Port: intstr.FromInt(telemetryPort),
									},
								},
								InitialDelaySeconds: 10,
								PeriodSeconds:       20,
								TimeoutSeconds:      1,
								SuccessThreshold:    1,
								FailureThreshold:    3,
							},
							ReadinessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									TCPSocket: &corev1.TCPSocketAction{
										Port: intstr.FromInt(telemetryPort),
									},
								},
								InitialDelaySeconds: 5,
								PeriodSeconds:       10,
								TimeoutSeconds:      1,
								SuccessThreshold:    1,
								FailureThreshold:    3,
							},
							Resources:                defaultTelemetryResources(t.Resources),
							TerminationMessagePath:   "/dev/termination-log",
							TerminationMessagePolicy: corev1.TerminationMessageReadFile,
							SecurityContext: &corev1.SecurityContext{
								AllowPrivilegeEscalation: boolPtr(false),
								Capabilities: &corev1.Capabilities{
									Drop: []corev1.Capability{"ALL"},
								},
							},
						},
					},
					Volumes: volumes,
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: boolPtr(true),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					ServiceAccountName: fmt.Sprintf("%s-controller-manager", jumpstarter.Name),
				},
			},
		},
	}
}

// cleanupTelemetry removes telemetry resources when telemetry is disabled.
// Owned resources (Deployment, Service) are deleted; the CR's garbage collection
// will handle removing any cert-manager Certificate.
func (r *JumpstarterReconciler) cleanupTelemetry(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	deploymentName := fmt.Sprintf("%s-telemetry", jumpstarter.Name)
	dep := &appsv1.Deployment{}
	dep.Name = deploymentName
	dep.Namespace = jumpstarter.Namespace
	if err := r.Delete(ctx, dep); err != nil && !errors.IsNotFound(err) {
		return fmt.Errorf("failed to delete telemetry deployment: %w", err)
	} else if err == nil {
		log.Info("Deleted telemetry deployment", "name", deploymentName)
		r.emitEventf(jumpstarter, corev1.EventTypeNormal, "TelemetryDeploymentDeleted",
			"Telemetry deployment deleted: name=%s", deploymentName)
	}

	svc := &corev1.Service{}
	svc.Name = telemetryServiceName
	svc.Namespace = jumpstarter.Namespace
	if err := r.Delete(ctx, svc); err != nil && !errors.IsNotFound(err) {
		return fmt.Errorf("failed to delete telemetry service: %w", err)
	} else if err == nil {
		log.Info("Deleted telemetry service", "name", telemetryServiceName)
	}

	return nil
}

// GetTelemetryCertSecretName returns the name of the telemetry TLS secret.
func GetTelemetryCertSecretName(js *operatorv1alpha1.Jumpstarter) string {
	return js.Name + telemetryCertSuffix
}

// resolveTelemetryCA reads the CA certificate that exporters need to verify the
// telemetry TLS connection. For self-signed CA mode, the cert is in the CA secret;
// for external issuers, the user-provided caBundle is used.
func (r *JumpstarterReconciler) resolveTelemetryCA(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) (string, error) {
	if jumpstarter.Spec.CertManager.Server != nil && jumpstarter.Spec.CertManager.Server.IssuerRef != nil {
		if len(jumpstarter.Spec.CertManager.Server.IssuerRef.CABundle) > 0 {
			return string(jumpstarter.Spec.CertManager.Server.IssuerRef.CABundle), nil
		}
		return "", nil
	}

	// Self-signed CA mode — read from the CA secret created by cert-manager
	caSecretName := jumpstarter.Name + caCertificateSuffix
	caSecret := &corev1.Secret{}
	if err := r.Get(ctx, client.ObjectKey{Name: caSecretName, Namespace: jumpstarter.Namespace}, caSecret); err != nil {
		return "", fmt.Errorf("CA secret %s not found: %w", caSecretName, err)
	}
	if cert, ok := caSecret.Data["tls.crt"]; ok {
		return string(cert), nil
	}
	return "", fmt.Errorf("CA secret %s missing tls.crt", caSecretName)
}

// telemetryEndpointFor returns the in-cluster gRPC endpoint for the telemetry service.
func telemetryEndpointFor(namespace string) string {
	return fmt.Sprintf("%s.%s.svc:%d", telemetryServiceName, namespace, telemetryPort)
}

// telemetryLabels returns the standard labels for telemetry resources.
func telemetryLabels(jumpstarter *operatorv1alpha1.Jumpstarter) map[string]string {
	return map[string]string{
		"component":  "telemetry",
		"app":        telemetryComponentApp,
		"controller": jumpstarter.Name,
	}
}

// defaultTelemetryResources returns sensible defaults for the telemetry pod if no
// explicit resource requirements are provided.
func defaultTelemetryResources(spec corev1.ResourceRequirements) corev1.ResourceRequirements {
	if len(spec.Requests) == 0 && len(spec.Limits) == 0 && len(spec.Claims) == 0 {
		return corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("50m"),
				corev1.ResourceMemory: resource.MustParse("128Mi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("500m"),
				corev1.ResourceMemory: resource.MustParse("256Mi"),
			},
		}
	}
	return spec
}
