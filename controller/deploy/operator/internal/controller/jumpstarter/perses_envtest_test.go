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

package jumpstarter

import (
	"os"
	"path/filepath"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/yaml"
)

var _ = Describe("Perses dashboard fixture", func() {
	It("is accepted by the PersesDashboard CRD", func() {
		namespace := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "perses-"}}
		Expect(k8sClient.Create(ctx, namespace)).To(Succeed())
		DeferCleanup(func() {
			_ = k8sClient.Delete(ctx, namespace)
		})

		raw, err := os.ReadFile(filepath.Join("..", "..", "..", "config", "test", "perses", "minimal-dashboard.yaml"))
		Expect(err).NotTo(HaveOccurred())
		var obj map[string]any
		Expect(yaml.Unmarshal(raw, &obj)).To(Succeed())
		dashboard := &unstructured.Unstructured{Object: obj}
		dashboard.SetNamespace(namespace.Name)

		Expect(k8sClient.Create(ctx, dashboard)).To(Succeed())

		fetched := &unstructured.Unstructured{}
		fetched.SetGroupVersionKind(dashboard.GroupVersionKind())
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: dashboard.GetName(), Namespace: namespace.Name,
		}, fetched)).To(Succeed())

		spec, ok := fetched.Object["spec"].(map[string]any)
		Expect(ok).To(BeTrue())
		Expect(spec).To(HaveKey("instanceSelector"))
		Expect(spec).To(HaveKey("config"))
	})
})
