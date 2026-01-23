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
	"context"
	"fmt"

	certmanagerv1 "github.com/cert-manager/cert-manager/pkg/apis/certmanager/v1"
	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

// updateStatus updates the status conditions of the Jumpstarter resource
func (r *JumpstarterReconciler) updateStatus(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// Track overall readiness
	allReady := true
	var messages []string

	// Check cert-manager related conditions if enabled
	if js.Spec.CertManager.Enabled {
		// Check if cert-manager CRDs are available
		certManagerAvailable := r.checkCertManagerCRDs(ctx)
		setCondition(js, operatorv1alpha1.ConditionTypeCertManagerAvailable,
			certManagerAvailable,
			conditionReason(certManagerAvailable, "CRDsInstalled", "CRDsNotFound"),
			conditionMessage(certManagerAvailable, "cert-manager CRDs are available", "cert-manager CRDs not found in cluster"))
		if !certManagerAvailable {
			allReady = false
			messages = append(messages, "cert-manager CRDs not available")
		}

		// Check issuer readiness
		issuerReady, issuerMsg := r.checkIssuerReady(ctx, js)
		setCondition(js, operatorv1alpha1.ConditionTypeIssuerReady,
			issuerReady,
			conditionReason(issuerReady, "IssuerReady", "IssuerNotReady"),
			issuerMsg)
		if !issuerReady {
			allReady = false
			messages = append(messages, issuerMsg)
		}

		// Check controller certificate readiness
		controllerCertReady, certMsg := r.checkControllerCertificateReady(ctx, js)
		setCondition(js, operatorv1alpha1.ConditionTypeControllerCertificateReady,
			controllerCertReady,
			conditionReason(controllerCertReady, "CertificateReady", "CertificatePending"),
			certMsg)
		if !controllerCertReady {
			allReady = false
			messages = append(messages, certMsg)
		}

		// Check router certificates readiness
		routerCertsReady, routerMsg := r.checkRouterCertificatesReady(ctx, js)
		setCondition(js, operatorv1alpha1.ConditionTypeRouterCertificatesReady,
			routerCertsReady,
			conditionReason(routerCertsReady, "AllCertificatesReady", "CertificatesPending"),
			routerMsg)
		if !routerCertsReady {
			allReady = false
			messages = append(messages, routerMsg)
		}
	}

	// Check controller deployment readiness
	controllerReady, controllerMsg := r.checkControllerDeploymentReady(ctx, js)
	setCondition(js, operatorv1alpha1.ConditionTypeControllerDeploymentReady,
		controllerReady,
		conditionReason(controllerReady, "DeploymentAvailable", "DeploymentNotAvailable"),
		controllerMsg)
	if !controllerReady {
		allReady = false
		messages = append(messages, controllerMsg)
	}

	// Check router deployments readiness
	routersReady, routersMsg := r.checkRouterDeploymentsReady(ctx, js)
	setCondition(js, operatorv1alpha1.ConditionTypeRouterDeploymentsReady,
		routersReady,
		conditionReason(routersReady, "AllDeploymentsAvailable", "DeploymentsNotAvailable"),
		routersMsg)
	if !routersReady {
		allReady = false
		messages = append(messages, routersMsg)
	}

	// Set overall Ready condition
	readyMessage := "All components are ready"
	if !allReady {
		readyMessage = fmt.Sprintf("Components not ready: %v", messages)
	}
	setCondition(js, operatorv1alpha1.ConditionTypeReady,
		allReady,
		conditionReason(allReady, "AllComponentsReady", "ComponentsNotReady"),
		readyMessage)

	// Update the status
	if err := r.Status().Update(ctx, js); err != nil {
		log.Error(err, "Failed to update Jumpstarter status")
		return err
	}

	log.V(1).Info("Status updated", "ready", allReady)
	return nil
}

// checkCertManagerCRDs checks if cert-manager CRDs are installed in the cluster
func (r *JumpstarterReconciler) checkCertManagerCRDs(ctx context.Context) bool {
	// Try to list Issuers - if this works, cert-manager CRDs are installed
	issuerList := &certmanagerv1.IssuerList{}
	err := r.List(ctx, issuerList, client.Limit(1))
	if err != nil {
		// If error is because CRD is not installed, return false
		if meta.IsNoMatchError(err) {
			return false
		}
		// Other errors (like RBAC) - assume CRDs exist but we have access issues
		// Log and return true to not block on transient errors
		logf.FromContext(ctx).V(1).Info("Error checking cert-manager CRDs", "error", err)
		return true
	}
	return true
}

// checkIssuerReady checks if the configured issuer is ready
func (r *JumpstarterReconciler) checkIssuerReady(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string) {
	var issuerName string
	var issuerKind string

	// Determine which issuer to check
	if js.Spec.CertManager.Server != nil && js.Spec.CertManager.Server.IssuerRef != nil {
		// External issuer
		issuerName = js.Spec.CertManager.Server.IssuerRef.Name
		issuerKind = js.Spec.CertManager.Server.IssuerRef.Kind
	} else {
		// Self-signed CA issuer
		issuerName = js.Name + caIssuerSuffix
		issuerKind = "Issuer"
	}

	if issuerKind == "ClusterIssuer" {
		clusterIssuer := &certmanagerv1.ClusterIssuer{}
		err := r.Get(ctx, types.NamespacedName{Name: issuerName}, clusterIssuer)
		if err != nil {
			if errors.IsNotFound(err) {
				return false, fmt.Sprintf("ClusterIssuer %s not found", issuerName)
			}
			return false, fmt.Sprintf("Error getting ClusterIssuer %s: %v", issuerName, err)
		}
		// Check Ready condition
		for _, cond := range clusterIssuer.Status.Conditions {
			if cond.Type == certmanagerv1.IssuerConditionReady {
				if cond.Status == "True" {
					return true, fmt.Sprintf("ClusterIssuer %s is ready", issuerName)
				}
				return false, fmt.Sprintf("ClusterIssuer %s not ready: %s", issuerName, cond.Message)
			}
		}
		return false, fmt.Sprintf("ClusterIssuer %s has no Ready condition", issuerName)
	}

	// Namespaced Issuer
	issuer := &certmanagerv1.Issuer{}
	err := r.Get(ctx, types.NamespacedName{Name: issuerName, Namespace: js.Namespace}, issuer)
	if err != nil {
		if errors.IsNotFound(err) {
			return false, fmt.Sprintf("Issuer %s not found", issuerName)
		}
		return false, fmt.Sprintf("Error getting Issuer %s: %v", issuerName, err)
	}
	// Check Ready condition
	for _, cond := range issuer.Status.Conditions {
		if cond.Type == certmanagerv1.IssuerConditionReady {
			if cond.Status == "True" {
				return true, fmt.Sprintf("Issuer %s is ready", issuerName)
			}
			return false, fmt.Sprintf("Issuer %s not ready: %s", issuerName, cond.Message)
		}
	}
	return false, fmt.Sprintf("Issuer %s has no Ready condition", issuerName)
}

// checkControllerCertificateReady checks if the controller TLS certificate secret exists
func (r *JumpstarterReconciler) checkControllerCertificateReady(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string) {
	secretName := GetControllerCertSecretName(js)
	secret := &corev1.Secret{}
	err := r.Get(ctx, types.NamespacedName{Name: secretName, Namespace: js.Namespace}, secret)
	if err != nil {
		if errors.IsNotFound(err) {
			return false, fmt.Sprintf("Controller TLS secret %s not found (certificate pending)", secretName)
		}
		return false, fmt.Sprintf("Error getting controller TLS secret: %v", err)
	}

	// Check if secret has tls.crt and tls.key
	if _, ok := secret.Data["tls.crt"]; !ok {
		return false, fmt.Sprintf("Controller TLS secret %s missing tls.crt", secretName)
	}
	if _, ok := secret.Data["tls.key"]; !ok {
		return false, fmt.Sprintf("Controller TLS secret %s missing tls.key", secretName)
	}

	return true, fmt.Sprintf("Controller TLS certificate ready (secret %s)", secretName)
}

// checkRouterCertificatesReady checks if all router TLS certificate secrets exist
func (r *JumpstarterReconciler) checkRouterCertificatesReady(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string) {
	allReady := true
	var notReadyRouters []int32

	for i := int32(0); i < js.Spec.Routers.Replicas; i++ {
		secretName := GetRouterCertSecretName(js, i)
		secret := &corev1.Secret{}
		err := r.Get(ctx, types.NamespacedName{Name: secretName, Namespace: js.Namespace}, secret)
		if err != nil {
			allReady = false
			notReadyRouters = append(notReadyRouters, i)
			continue
		}

		// Check if secret has tls.crt and tls.key
		if _, ok := secret.Data["tls.crt"]; !ok {
			allReady = false
			notReadyRouters = append(notReadyRouters, i)
			continue
		}
		if _, ok := secret.Data["tls.key"]; !ok {
			allReady = false
			notReadyRouters = append(notReadyRouters, i)
		}
	}

	if allReady {
		return true, fmt.Sprintf("All %d router TLS certificates ready", js.Spec.Routers.Replicas)
	}
	return false, fmt.Sprintf("Router TLS certificates pending for replicas: %v", notReadyRouters)
}

// checkControllerDeploymentReady checks if the controller deployment is available
func (r *JumpstarterReconciler) checkControllerDeploymentReady(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string) {
	deploymentName := fmt.Sprintf("%s-controller", js.Name)
	deployment := &appsv1.Deployment{}
	err := r.Get(ctx, types.NamespacedName{Name: deploymentName, Namespace: js.Namespace}, deployment)
	if err != nil {
		if errors.IsNotFound(err) {
			return false, fmt.Sprintf("Controller deployment %s not found", deploymentName)
		}
		return false, fmt.Sprintf("Error getting controller deployment: %v", err)
	}

	// Check Available condition
	for _, cond := range deployment.Status.Conditions {
		if cond.Type == appsv1.DeploymentAvailable {
			if cond.Status == corev1.ConditionTrue {
				return true, fmt.Sprintf("Controller deployment %s is available", deploymentName)
			}
			return false, fmt.Sprintf("Controller deployment %s not available: %s", deploymentName, cond.Message)
		}
	}

	return false, fmt.Sprintf("Controller deployment %s has no Available condition", deploymentName)
}

// checkRouterDeploymentsReady checks if all router deployments are available
func (r *JumpstarterReconciler) checkRouterDeploymentsReady(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (bool, string) {
	allReady := true
	var notReadyRouters []int32

	for i := int32(0); i < js.Spec.Routers.Replicas; i++ {
		deploymentName := fmt.Sprintf("%s-router-%d", js.Name, i)
		deployment := &appsv1.Deployment{}
		err := r.Get(ctx, types.NamespacedName{Name: deploymentName, Namespace: js.Namespace}, deployment)
		if err != nil {
			allReady = false
			notReadyRouters = append(notReadyRouters, i)
			continue
		}

		// Check Available condition
		available := false
		for _, cond := range deployment.Status.Conditions {
			if cond.Type == appsv1.DeploymentAvailable && cond.Status == corev1.ConditionTrue {
				available = true
				break
			}
		}
		if !available {
			allReady = false
			notReadyRouters = append(notReadyRouters, i)
		}
	}

	if allReady {
		return true, fmt.Sprintf("All %d router deployments available", js.Spec.Routers.Replicas)
	}
	return false, fmt.Sprintf("Router deployments not available for replicas: %v", notReadyRouters)
}

// setCondition sets a condition on the Jumpstarter status
func setCondition(js *operatorv1alpha1.Jumpstarter, conditionType string, status bool, reason, message string) {
	conditionStatus := metav1.ConditionFalse
	if status {
		conditionStatus = metav1.ConditionTrue
	}

	meta.SetStatusCondition(&js.Status.Conditions, metav1.Condition{
		Type:               conditionType,
		Status:             conditionStatus,
		Reason:             reason,
		Message:            message,
		ObservedGeneration: js.Generation,
	})
}

// conditionReason returns the appropriate reason based on the status
func conditionReason(status bool, trueReason, falseReason string) string {
	if status {
		return trueReason
	}
	return falseReason
}

// conditionMessage returns the appropriate message based on the status
func conditionMessage(status bool, trueMessage, falseMessage string) string {
	if status {
		return trueMessage
	}
	return falseMessage
}
