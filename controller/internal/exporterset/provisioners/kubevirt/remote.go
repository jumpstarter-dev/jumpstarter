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

package kubevirt

import (
	"context"
	"fmt"
	"strings"
	"sync"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/tools/clientcmd"
	kubevirtv1 "kubevirt.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// remoteClientCache caches controller-runtime clients keyed by
// "namespace/name/secretResourceVersion" so that same-named VTCs in different
// namespaces never collide, and credential rotation invalidates the entry.
type remoteClientCache struct {
	mu      sync.RWMutex
	clients map[string]client.Client
}

func newRemoteClientCache() *remoteClientCache {
	return &remoteClientCache{
		clients: make(map[string]client.Client),
	}
}

func (c *remoteClientCache) get(key string) (client.Client, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	cl, ok := c.clients[key]
	return cl, ok
}

func (c *remoteClientCache) set(key string, cl client.Client) {
	c.mu.Lock()
	defer c.mu.Unlock()
	prefix := key[:strings.LastIndex(key, "/")+1]
	for k := range c.clients {
		if strings.HasPrefix(k, prefix) && k != key {
			delete(c.clients, k)
		}
	}
	c.clients[key] = cl
}

var (
	remoteSchemeOnce   sync.Once
	cachedRemoteScheme *runtime.Scheme
)

func remoteScheme() *runtime.Scheme {
	remoteSchemeOnce.Do(func() {
		cachedRemoteScheme = runtime.NewScheme()
		_ = corev1.AddToScheme(cachedRemoteScheme)
		_ = kubevirtv1.AddToScheme(cachedRemoteScheme)
	})
	return cachedRemoteScheme
}

// getRemoteClient returns a controller-runtime client for the remote KubeVirt
// cluster. It reads the kubeconfig from the Secret referenced by the VTC's
// CredentialsSecretRef and caches the client per namespace/name/resourceVersion.
func (p *Provisioner) getRemoteClient(
	ctx context.Context,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
) (client.Client, error) {
	if vtc.Spec.CredentialsSecretRef == nil {
		return nil, fmt.Errorf("VirtualTargetClass %q has no credentialsSecretRef", vtc.Name)
	}

	secret := &corev1.Secret{}
	secretKey := types.NamespacedName{
		Name:      vtc.Spec.CredentialsSecretRef.Name,
		Namespace: vtc.Namespace,
	}
	if err := p.Client.Get(ctx, secretKey, secret); err != nil {
		return nil, fmt.Errorf("read credentials Secret %s/%s: %w", secretKey.Namespace, secretKey.Name, err)
	}

	cacheKey := fmt.Sprintf("%s/%s/%s", vtc.Namespace, vtc.Name, secret.ResourceVersion)
	if cl, ok := p.remoteClients.get(cacheKey); ok {
		return cl, nil
	}

	kubeconfig, ok := secret.Data["kubeconfig"]
	if !ok {
		return nil, fmt.Errorf("credentials Secret %s/%s missing \"kubeconfig\" key", secretKey.Namespace, secretKey.Name)
	}

	restConfig, err := clientcmd.RESTConfigFromKubeConfig(kubeconfig)
	if err != nil {
		return nil, fmt.Errorf("parse kubeconfig from Secret %s/%s: %w", secretKey.Namespace, secretKey.Name, err)
	}

	cl, err := client.New(restConfig, client.Options{
		Scheme: remoteScheme(),
	})
	if err != nil {
		return nil, fmt.Errorf("create remote client for VTC %s/%s: %w", vtc.Namespace, vtc.Name, err)
	}

	p.remoteClients.set(cacheKey, cl)
	return cl, nil
}
