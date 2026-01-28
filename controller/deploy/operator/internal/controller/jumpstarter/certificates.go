/*
Copyright 2025. The Jumpstarter Authors.

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
	"net"
	"time"

	certmanagerv1 "github.com/cert-manager/cert-manager/pkg/apis/certmanager/v1"
	cmmeta "github.com/cert-manager/cert-manager/pkg/apis/meta/v1"
	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

const (
	// Default certificate durations
	defaultCADuration   = 87600 * time.Hour // 10 years
	defaultCertDuration = 8760 * time.Hour  // 1 year
	defaultRenewBefore  = 360 * time.Hour   // 15 days

	// Issuer and Certificate naming
	selfSignedIssuerSuffix = "-selfsigned-issuer"
	caIssuerSuffix         = "-ca-issuer"
	caCertificateSuffix    = "-ca"
	controllerCertSuffix   = "-controller-tls"
	routerCertSuffix       = "-router-%d-tls"
)

// getServerCertDurationSettings
// extracts certificate duration settings from the Jumpstarter spec.
// Returns caDuration, certDuration, and renewBefore with defaults applied if not configured.
func getServerCertDurationSettings(js *operatorv1alpha1.Jumpstarter) (time.Duration, time.Duration, time.Duration) {
	caDuration := defaultCADuration
	certDuration := defaultCertDuration
	renewBefore := defaultRenewBefore

	if js.Spec.CertManager.Server != nil && js.Spec.CertManager.Server.SelfSigned != nil {
		cfg := js.Spec.CertManager.Server.SelfSigned
		if cfg.CADuration != nil {
			caDuration = cfg.CADuration.Duration
		}
		if cfg.CertDuration != nil {
			certDuration = cfg.CertDuration.Duration
		}
		if cfg.RenewBefore != nil {
			renewBefore = cfg.RenewBefore.Duration
		}
	}

	return caDuration, certDuration, renewBefore
}

// reconcileCertificates reconciles all cert-manager resources for TLS certificates.
// This is the main entry point called from Reconcile() when cert-manager is enabled.
func (r *JumpstarterReconciler) reconcileCertificates(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	if !js.Spec.CertManager.Enabled {
		// If cert-manager integration is disabled, skip certificate reconciliation
		// we do not remove existing certificates or issuers here,
		// that must be handled by the administrator, to avoid issues if the certificates
		// are disabled by error, enabled again, at least the certificates will remain.
		log.V(1).Info("cert-manager integration disabled, skipping certificate reconciliation")
		return nil
	}

	log.Info("Reconciling cert-manager resources")

	// Determine issuer reference based on configuration
	issuerRef, err := r.reconcileIssuer(ctx, js)
	if err != nil {
		return fmt.Errorf("failed to reconcile issuer: %w", err)
	}

	// Skip certificate creation if no issuer is configured
	if issuerRef.Name == "" {
		log.Info("No issuer configured, skipping certificate creation")
		return nil
	}

	// Create controller certificate
	if err := r.reconcileControllerCertificate(ctx, js, issuerRef); err != nil {
		return fmt.Errorf("failed to reconcile controller certificate: %w", err)
	}

	// Create router certificates (one per replica)
	for i := int32(0); i < js.Spec.Routers.Replicas; i++ {
		if err := r.reconcileRouterCertificate(ctx, js, issuerRef, i); err != nil {
			return fmt.Errorf("failed to reconcile router %d certificate: %w", i, err)
		}
	}

	return nil
}

// reconcileIssuer reconciles the cert-manager Issuer based on configuration.
// Returns the issuer reference to use for certificate issuance.
func (r *JumpstarterReconciler) reconcileIssuer(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (cmmeta.ObjectReference, error) {
	log := logf.FromContext(ctx)

	// If external issuer is specified, use it directly
	if js.Spec.CertManager.Server != nil && js.Spec.CertManager.Server.IssuerRef != nil {
		ref := js.Spec.CertManager.Server.IssuerRef
		log.Info("Using external issuer", "name", ref.Name, "kind", ref.Kind)
		return cmmeta.ObjectReference{
			Name:  ref.Name,
			Kind:  ref.Kind,
			Group: ref.Group,
		}, nil
	}

	// Check if self-signed mode is enabled
	// Default to true if SelfSigned config is nil or Enabled is not explicitly set
	selfSignedEnabled := true
	if js.Spec.CertManager.Server != nil && js.Spec.CertManager.Server.SelfSigned != nil {
		selfSignedEnabled = js.Spec.CertManager.Server.SelfSigned.Enabled
	}

	if selfSignedEnabled {
		log.Info("Using self-signed CA mode")
		return r.reconcileSelfSignedIssuer(ctx, js)
	}

	// Self-signed is disabled and no external issuer is configured
	// Return empty issuer reference - status update will handle the error condition
	log.Info("Self-signed CA is disabled and no external issuer configured, skipping issuer creation")
	return cmmeta.ObjectReference{}, nil
}

// reconcileSelfSignedIssuer creates the self-signed CA infrastructure:
// 1. A SelfSigned Issuer (bootstrap)
// 2. A CA Certificate signed by the self-signed issuer
// 3. A CA Issuer that uses the CA certificate's secret
func (r *JumpstarterReconciler) reconcileSelfSignedIssuer(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (cmmeta.ObjectReference, error) {
	log := logf.FromContext(ctx)

	// Get duration settings
	caDuration, _, renewBefore := getServerCertDurationSettings(js)

	// 1. Create SelfSigned Issuer (bootstrap issuer)
	selfSignedIssuerName := js.Name + selfSignedIssuerSuffix
	selfSignedIssuer := &certmanagerv1.Issuer{
		ObjectMeta: metav1.ObjectMeta{
			Name:      selfSignedIssuerName,
			Namespace: js.Namespace,
			Labels: map[string]string{
				"app":                          js.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		Spec: certmanagerv1.IssuerSpec{
			IssuerConfig: certmanagerv1.IssuerConfig{
				SelfSigned: &certmanagerv1.SelfSignedIssuer{},
			},
		},
	}

	if err := r.reconcileIssuerResource(ctx, js, selfSignedIssuer); err != nil {
		return cmmeta.ObjectReference{}, fmt.Errorf("failed to reconcile self-signed issuer: %w", err)
	}
	log.Info("Reconciled self-signed issuer", "name", selfSignedIssuerName)

	// 2. Create CA Certificate signed by the self-signed issuer
	caCertName := js.Name + caCertificateSuffix

	// Ensure renewBefore is less than duration
	caRenewBefore := renewBefore
	if renewBefore >= caDuration {
		caRenewBefore = caDuration / 2
		log.V(1).Info("Capping CA certificate renewBefore duration",
			"configured", renewBefore,
			"caDuration", caDuration,
			"adjusted", caRenewBefore)
	}

	caCert := &certmanagerv1.Certificate{
		ObjectMeta: metav1.ObjectMeta{
			Name:      caCertName,
			Namespace: js.Namespace,
			Labels: map[string]string{
				"app":                          js.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		Spec: certmanagerv1.CertificateSpec{
			IsCA:        true,
			CommonName:  fmt.Sprintf("%s-ca", js.Name),
			SecretName:  caCertName,
			Duration:    &metav1.Duration{Duration: caDuration},
			RenewBefore: &metav1.Duration{Duration: caRenewBefore},
			PrivateKey: &certmanagerv1.CertificatePrivateKey{
				Algorithm: certmanagerv1.ECDSAKeyAlgorithm,
				Size:      256,
			},
			IssuerRef: cmmeta.ObjectReference{
				Name:  selfSignedIssuerName,
				Kind:  "Issuer",
				Group: "cert-manager.io",
			},
		},
	}

	if err := r.reconcileCertificateResource(ctx, js, caCert); err != nil {
		return cmmeta.ObjectReference{}, fmt.Errorf("failed to reconcile CA certificate: %w", err)
	}
	log.Info("Reconciled CA certificate", "name", caCertName)

	// 3. Create CA Issuer that uses the CA certificate's secret
	caIssuerName := js.Name + caIssuerSuffix
	caIssuer := &certmanagerv1.Issuer{
		ObjectMeta: metav1.ObjectMeta{
			Name:      caIssuerName,
			Namespace: js.Namespace,
			Labels: map[string]string{
				"app":                          js.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		Spec: certmanagerv1.IssuerSpec{
			IssuerConfig: certmanagerv1.IssuerConfig{
				CA: &certmanagerv1.CAIssuer{
					SecretName: caCertName,
				},
			},
		},
	}

	if err := r.reconcileIssuerResource(ctx, js, caIssuer); err != nil {
		return cmmeta.ObjectReference{}, fmt.Errorf("failed to reconcile CA issuer: %w", err)
	}
	log.Info("Reconciled CA issuer", "name", caIssuerName)

	return cmmeta.ObjectReference{
		Name:  caIssuerName,
		Kind:  "Issuer",
		Group: "cert-manager.io",
	}, nil
}

// reconcileServerCertificate is a helper that creates a TLS certificate for a server component.
func (r *JumpstarterReconciler) reconcileServerCertificate(
	ctx context.Context,
	js *operatorv1alpha1.Jumpstarter,
	issuerRef cmmeta.ObjectReference,
	certName string,
	component string,
	dnsNames []string,
	extraLabels map[string]string,
) error {
	log := logf.FromContext(ctx)

	// Get duration settings
	_, certDuration, renewBefore := getServerCertDurationSettings(js)

	// Ensure renewBefore is less than duration
	adjustedRenewBefore := renewBefore
	if renewBefore >= certDuration {
		adjustedRenewBefore = certDuration / 2
		logFields := []interface{}{
			"component", component,
			"configured", renewBefore,
			"certDuration", certDuration,
			"adjusted", adjustedRenewBefore,
		}
		log.V(1).Info("Capping certificate renewBefore duration", logFields...)
	}

	// Build labels
	labels := map[string]string{
		"app":                          js.Name,
		"app.kubernetes.io/managed-by": "jumpstarter-operator",
		"component":                    component,
	}
	for k, v := range extraLabels {
		labels[k] = v
	}

	// Separate IP addresses from DNS names for cert-manager v1 compatibility
	var dns []string
	var ipAddrs []string
	for _, name := range dnsNames {
		if ip := net.ParseIP(name); ip != nil {
			ipAddrs = append(ipAddrs, name)
		} else {
			dns = append(dns, name)
		}
	}

	cert := &certmanagerv1.Certificate{
		ObjectMeta: metav1.ObjectMeta{
			Name:      certName,
			Namespace: js.Namespace,
			Labels:    labels,
		},
		Spec: certmanagerv1.CertificateSpec{
			SecretName:  certName,
			Duration:    &metav1.Duration{Duration: certDuration},
			RenewBefore: &metav1.Duration{Duration: adjustedRenewBefore},
			PrivateKey: &certmanagerv1.CertificatePrivateKey{
				Algorithm: certmanagerv1.ECDSAKeyAlgorithm,
				Size:      256,
			},
			DNSNames:    dns,
			IPAddresses: ipAddrs,
			IssuerRef:   issuerRef,
			Usages: []certmanagerv1.KeyUsage{
				certmanagerv1.UsageServerAuth,
				certmanagerv1.UsageDigitalSignature,
				certmanagerv1.UsageKeyEncipherment,
			},
		},
	}

	if err := r.reconcileCertificateResource(ctx, js, cert); err != nil {
		return err
	}

	log.Info("Reconciled certificate", "name", certName, "component", component, "dnsNames", dnsNames)
	return nil
}

// reconcileControllerCertificate creates the TLS certificate for the controller.
func (r *JumpstarterReconciler) reconcileControllerCertificate(ctx context.Context, js *operatorv1alpha1.Jumpstarter, issuerRef cmmeta.ObjectReference) error {
	certName := js.Name + controllerCertSuffix
	dnsNames := r.collectControllerDNSNames(js)
	return r.reconcileServerCertificate(ctx, js, issuerRef, certName, "controller", dnsNames, nil)
}

// reconcileRouterCertificate creates the TLS certificate for a specific router replica.
func (r *JumpstarterReconciler) reconcileRouterCertificate(ctx context.Context, js *operatorv1alpha1.Jumpstarter, issuerRef cmmeta.ObjectReference, replicaIndex int32) error {
	certName := fmt.Sprintf(js.Name+routerCertSuffix, replicaIndex)
	dnsNames := r.collectRouterDNSNames(js, replicaIndex)
	extraLabels := map[string]string{
		"router-index": fmt.Sprintf("%d", replicaIndex),
	}
	return r.reconcileServerCertificate(ctx, js, issuerRef, certName, "router", dnsNames, extraLabels)
}

// collectControllerDNSNames collects all DNS names for the controller certificate.
func (r *JumpstarterReconciler) collectControllerDNSNames(js *operatorv1alpha1.Jumpstarter) []string {
	dnsNames := make([]string, 0)

	// Add default controller service name
	dnsNames = append(dnsNames,
		fmt.Sprintf("%s-controller", js.Name),
		fmt.Sprintf("%s-controller.%s", js.Name, js.Namespace),
		fmt.Sprintf("%s-controller.%s.svc", js.Name, js.Namespace),
		fmt.Sprintf("%s-controller.%s.svc.cluster.local", js.Name, js.Namespace),
	)

	// Add DNS names from configured endpoints
	for _, endpoint := range js.Spec.Controller.GRPC.Endpoints {
		if endpoint.Address != "" {
			host := extractHostname(endpoint.Address)
			if host != "" && !contains(dnsNames, host) {
				dnsNames = append(dnsNames, host)
			}
		}
	}

	// Add default domain-based name
	if js.Spec.BaseDomain != "" {
		defaultName := fmt.Sprintf("grpc.%s", js.Spec.BaseDomain)
		if !contains(dnsNames, defaultName) {
			dnsNames = append(dnsNames, defaultName)
		}
	}

	return dnsNames
}

// collectRouterDNSNames collects all DNS names for a specific router replica certificate.
func (r *JumpstarterReconciler) collectRouterDNSNames(js *operatorv1alpha1.Jumpstarter, replicaIndex int32) []string {
	dnsNames := make([]string, 0)

	// Add default router service name
	serviceName := fmt.Sprintf("%s-router-%d", js.Name, replicaIndex)
	dnsNames = append(dnsNames,
		serviceName,
		fmt.Sprintf("%s.%s", serviceName, js.Namespace),
		fmt.Sprintf("%s.%s.svc", serviceName, js.Namespace),
		fmt.Sprintf("%s.%s.svc.cluster.local", serviceName, js.Namespace),
	)

	// Add DNS names from configured endpoints (with replica substitution)
	for _, endpoint := range js.Spec.Routers.GRPC.Endpoints {
		if endpoint.Address != "" {
			address := r.substituteReplica(endpoint.Address, replicaIndex)
			host := extractHostname(address)
			if host != "" && !contains(dnsNames, host) {
				dnsNames = append(dnsNames, host)
			}
		}
	}

	// Add default domain-based name
	if js.Spec.BaseDomain != "" {
		defaultName := fmt.Sprintf("router-%d.%s", replicaIndex, js.Spec.BaseDomain)
		if !contains(dnsNames, defaultName) {
			dnsNames = append(dnsNames, defaultName)
		}
	}

	return dnsNames
}

// reconcileIssuerResource creates or updates an Issuer resource.
func (r *JumpstarterReconciler) reconcileIssuerResource(ctx context.Context, js *operatorv1alpha1.Jumpstarter, issuer *certmanagerv1.Issuer) error {
	existing := &certmanagerv1.Issuer{}
	existing.Name = issuer.Name
	existing.Namespace = issuer.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existing, func() error {
		existing.Labels = issuer.Labels
		existing.Spec = issuer.Spec
		return controllerutil.SetControllerReference(js, existing, r.Scheme)
	})

	if err != nil {
		return fmt.Errorf("failed to reconcile issuer %s: %w", issuer.Name, err)
	}

	logf.FromContext(ctx).V(1).Info("Issuer reconciled", "name", issuer.Name, "operation", op)
	return nil
}

// reconcileCertificateResource creates or updates a Certificate resource.
func (r *JumpstarterReconciler) reconcileCertificateResource(ctx context.Context, js *operatorv1alpha1.Jumpstarter, cert *certmanagerv1.Certificate) error {
	existing := &certmanagerv1.Certificate{}
	existing.Name = cert.Name
	existing.Namespace = cert.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existing, func() error {
		existing.Labels = cert.Labels
		existing.Spec = cert.Spec
		return controllerutil.SetControllerReference(js, existing, r.Scheme)
	})

	if err != nil {
		return fmt.Errorf("failed to reconcile certificate %s: %w", cert.Name, err)
	}

	logf.FromContext(ctx).V(1).Info("Certificate reconciled", "name", cert.Name, "operation", op)
	return nil
}

// GetControllerCertSecretName returns the name of the controller TLS secret.
func GetControllerCertSecretName(js *operatorv1alpha1.Jumpstarter) string {
	return js.Name + controllerCertSuffix
}

// GetRouterCertSecretName returns the name of a router TLS secret.
func GetRouterCertSecretName(js *operatorv1alpha1.Jumpstarter, replicaIndex int32) string {
	return fmt.Sprintf(js.Name+routerCertSuffix, replicaIndex)
}

// extractHostname extracts the hostname from an address (removes port if present).
// It properly handles IPv4, IPv6 (bracketed), and host:port forms.
func extractHostname(address string) string {
	// Try to split host and port using net.SplitHostPort
	// This correctly handles [IPv6]:port, hostname:port, and IPv4:port
	host, _, err := net.SplitHostPort(address)
	if err == nil {
		// Successfully split host and port
		return host
	}

	// If SplitHostPort failed, there's no port in the address
	// Return the full address (handles plain IPv6, IPv4, or hostname)
	return address
}

// contains checks if a string slice contains a specific string.
func contains(slice []string, str string) bool {
	for _, s := range slice {
		if s == str {
			return true
		}
	}
	return false
}
