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

package e2e

import (
	"fmt"
	"os"
	"time"

	"github.com/google/uuid"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// CreateTestNamespace creates a unique test namespace with a UUID suffix.
// It returns the generated namespace name.
// The namespace name will be in the format: jumpstarter-e2e-{uuid}
func CreateTestNamespace() string {
	namespaceName := fmt.Sprintf("jumpstarter-e2e-%s", uuid.New().String())

	By(fmt.Sprintf("creating the test namespace %s", namespaceName))
	ns := &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{
			Name: namespaceName,
		},
	}
	err := k8sClient.Create(ctx, ns)
	Expect(err).NotTo(HaveOccurred(), "Failed to create test namespace")

	return namespaceName
}

// DeleteTestNamespace deletes the specified namespace and waits for it to be fully removed.
// It uses a 2-minute timeout to ensure the namespace is completely deleted.
func DeleteTestNamespace(namespaceName string) {
	By("deleting the test namespace")
	ns := &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{
			Name: namespaceName,
		},
	}
	_ = k8sClient.Delete(ctx, ns)

	// if environment variable E2E_NO_CLEANUP_WAIT is set, skip the wait
	if os.Getenv("E2E_NO_CLEANUP_WAIT") == "true" {
		return
	}
	By(fmt.Sprintf("waiting for namespace %s to be fully deleted", namespaceName))
	Eventually(func(g Gomega) {
		getErr := k8sClient.Get(ctx, types.NamespacedName{
			Name: namespaceName,
		}, ns)
		g.Expect(apierrors.IsNotFound(getErr)).To(BeTrue())
	}, 2*time.Minute).Should(Succeed())
}
