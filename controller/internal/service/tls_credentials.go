/*
Copyright 2026.

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

package service

import (
	"crypto/tls"
	"encoding/pem"
	"fmt"
	"net"
	"os"

	"google.golang.org/grpc/credentials"
)

// LoadTLSCertificate loads a TLS certificate from operator-mounted files or
// generates a self-signed one as fallback.
//
// If EXTERNAL_CERT_PEM and EXTERNAL_KEY_PEM env vars both point to valid PEM
// files (set by the operator via Secret volume mounts), those are used.
// Otherwise a self-signed certificate is generated for the given SANs.
//
// selfSignedPEM is non-empty only when a self-signed certificate was generated.
// Callers that advertise the certificate to clients (e.g. the telemetry service)
// should log it so the operator can pin it in the relevant ConfigMap field.
func LoadTLSCertificate(commonName string, dnsnames []string, ipaddresses []net.IP) (*tls.Certificate, string, error) {
	certPEMPath := os.Getenv("EXTERNAL_CERT_PEM")
	keyPEMPath := os.Getenv("EXTERNAL_KEY_PEM")

	// Require both or neither — a partial configuration means a broken Secret
	// mount that would cause clients trusting the intended certificate to fail TLS.
	if (certPEMPath == "") != (keyPEMPath == "") {
		return nil, "", fmt.Errorf("EXTERNAL_CERT_PEM and EXTERNAL_KEY_PEM must be set together; got cert=%q key=%q", certPEMPath, keyPEMPath)
	}

	if certPEMPath != "" {
		certPEMBytes, err := os.ReadFile(certPEMPath)
		if err != nil {
			return nil, "", fmt.Errorf("failed to read external certificate file: %w", err)
		}
		keyPEMBytes, err := os.ReadFile(keyPEMPath)
		if err != nil {
			return nil, "", fmt.Errorf("failed to read external key file: %w", err)
		}
		cert, err := tls.X509KeyPair(certPEMBytes, keyPEMBytes)
		if err != nil {
			return nil, "", fmt.Errorf("failed to parse external certificate: %w", err)
		}
		return &cert, "", nil
	}

	cert, err := NewSelfSignedCertificate(commonName, dnsnames, ipaddresses)
	if err != nil {
		return nil, "", err
	}
	selfSignedPEM := string(pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: cert.Certificate[0],
	}))
	return cert, selfSignedPEM, nil
}

// LoadTLSCredentials returns gRPC server TLS credentials built from
// LoadTLSCertificate, enforcing a minimum TLS version of 1.2.
func LoadTLSCredentials(commonName string, dnsnames []string, ipaddresses []net.IP) (credentials.TransportCredentials, string, error) {
	cert, selfSignedPEM, err := LoadTLSCertificate(commonName, dnsnames, ipaddresses)
	if err != nil {
		return nil, "", err
	}
	creds := credentials.NewTLS(&tls.Config{
		Certificates: []tls.Certificate{*cert},
		MinVersion:   tls.VersionTLS12,
	})
	return creds, selfSignedPEM, nil
}
