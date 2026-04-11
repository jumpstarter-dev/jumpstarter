/*
Copyright 2024.

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

package controller

import (
	"context"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"google.golang.org/protobuf/proto"
	descriptorpb "google.golang.org/protobuf/types/descriptorpb"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
)

// makeFileDescriptorProto creates serialized FileDescriptorProto bytes for a given package name.
func makeFileDescriptorProto(pkg string) []byte {
	fd := &descriptorpb.FileDescriptorProto{
		Package: proto.String(pkg),
	}
	data, err := proto.Marshal(fd)
	Expect(err).NotTo(HaveOccurred())
	return data
}

// reconcileExporterClass reconciles an ExporterClass and returns the result.
func reconcileExporterClass(ctx context.Context, name string) reconcile.Result {
	reconciler := &ExporterClassReconciler{
		Client: k8sClient,
		Scheme: k8sClient.Scheme(),
	}
	result, err := reconciler.Reconcile(ctx, reconcile.Request{
		NamespacedName: types.NamespacedName{
			Name:      name,
			Namespace: "default",
		},
	})
	Expect(err).NotTo(HaveOccurred())
	return result
}

func getExporterClass(ctx context.Context, name string) *jumpstarterdevv1alpha1.ExporterClass {
	ec := &jumpstarterdevv1alpha1.ExporterClass{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: "default",
	}, ec)
	Expect(err).NotTo(HaveOccurred())
	return ec
}

func getExporterForTest(ctx context.Context, name string) *jumpstarterdevv1alpha1.Exporter {
	exporter := &jumpstarterdevv1alpha1.Exporter{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: "default",
	}, exporter)
	Expect(err).NotTo(HaveOccurred())
	return exporter
}

// --- ExtractProtoPackage unit tests (no envtest needed) ---

var _ = Describe("ExtractProtoPackage", func() {
	It("should extract the package name from a valid FileDescriptorProto", func() {
		data := makeFileDescriptorProto("jumpstarter.interfaces.power.v1")
		Expect(ExtractProtoPackage(data)).To(Equal("jumpstarter.interfaces.power.v1"))
	})

	It("should return empty string for empty data", func() {
		Expect(ExtractProtoPackage([]byte{})).To(Equal(""))
	})

	It("should return empty string for invalid data", func() {
		Expect(ExtractProtoPackage([]byte{0xFF, 0xFF, 0xFF})).To(Equal(""))
	})

	It("should handle a FileDescriptorProto with multiple fields", func() {
		fd := &descriptorpb.FileDescriptorProto{
			Name:    proto.String("test.proto"),
			Package: proto.String("jumpstarter.interfaces.serial.v1"),
		}
		data, err := proto.Marshal(fd)
		Expect(err).NotTo(HaveOccurred())
		Expect(ExtractProtoPackage(data)).To(Equal("jumpstarter.interfaces.serial.v1"))
	})
})

// --- removeString unit tests ---

var _ = Describe("removeString", func() {
	It("should remove the target string from the slice", func() {
		result := removeString([]string{"a", "b", "c"}, "b")
		Expect(result).To(Equal([]string{"a", "c"}))
	})

	It("should return the same slice when target is not found", func() {
		result := removeString([]string{"a", "b", "c"}, "d")
		Expect(result).To(Equal([]string{"a", "b", "c"}))
	})

	It("should handle empty slice", func() {
		result := removeString([]string{}, "a")
		Expect(result).To(BeEmpty())
	})

	It("should remove all occurrences", func() {
		result := removeString([]string{"a", "b", "a", "c"}, "a")
		Expect(result).To(Equal([]string{"b", "c"}))
	})
})

// --- ExporterClass Controller envtest tests ---

var _ = Describe("ExporterClass Controller", func() {
	ctx := context.Background()

	// Helper to create a DriverInterface in the cluster.
	createDriverInterface := func(name, pkg string) {
		di := &jumpstarterdevv1alpha1.DriverInterface{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "default",
			},
			Spec: jumpstarterdevv1alpha1.DriverInterfaceSpec{
				Proto: jumpstarterdevv1alpha1.DriverInterfaceProto{
					Package: pkg,
				},
			},
		}
		Expect(k8sClient.Create(ctx, di)).To(Succeed())
	}

	deleteDriverInterface := func(name string) {
		di := &jumpstarterdevv1alpha1.DriverInterface{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "default",
			},
		}
		_ = k8sClient.Delete(ctx, di)
	}

	createExporterClass := func(name string, spec jumpstarterdevv1alpha1.ExporterClassSpec) {
		ec := &jumpstarterdevv1alpha1.ExporterClass{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "default",
			},
			Spec: spec,
		}
		Expect(k8sClient.Create(ctx, ec)).To(Succeed())
	}

	deleteExporterClass := func(name string) {
		ec := &jumpstarterdevv1alpha1.ExporterClass{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "default",
			},
		}
		_ = k8sClient.Delete(ctx, ec)
	}

	createTestExporter := func(name string, labels map[string]string, devices []jumpstarterdevv1alpha1.Device) {
		exporter := &jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "default",
				Labels:    labels,
			},
		}
		Expect(k8sClient.Create(ctx, exporter.DeepCopy())).To(Succeed())

		// Set devices on status.
		fresh := getExporterForTest(ctx, name)
		fresh.Status.Devices = devices
		Expect(k8sClient.Status().Update(ctx, fresh)).To(Succeed())
	}

	deleteTestExporter := func(name string) {
		exporter := &jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: "default",
			},
		}
		_ = k8sClient.Delete(ctx, exporter)
		// Clean up the credential secret (created by Exporter reconciler via createExporters).
		// Not needed here since we create exporters directly.
	}

	Describe("DriverInterface matching by proto package", func() {
		BeforeEach(func() {
			createDriverInterface("di-power", "jumpstarter.interfaces.power.v1")
			createDriverInterface("di-serial", "jumpstarter.interfaces.serial.v1")
		})

		AfterEach(func() {
			deleteDriverInterface("di-power")
			deleteDriverInterface("di-serial")
			deleteExporterClass("ec-match-test")
			deleteTestExporter("exporter-match-test")
		})

		It("should match an exporter with a FileDescriptorProto containing the correct package", func() {
			createTestExporter("exporter-match-test",
				map[string]string{"board": "test"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)

			createExporterClass("ec-match-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Selector: &metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "test"},
				},
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-match-test")

			ec := getExporterClass(ctx, "ec-match-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))
			Expect(meta.IsStatusConditionTrue(ec.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterClassConditionTypeReady))).To(BeTrue())

			exporter := getExporterForTest(ctx, "exporter-match-test")
			Expect(exporter.Status.SatisfiedExporterClasses).To(ContainElement("ec-match-test"))
		})

		It("should match an exporter with a jumpstarter.dev/interface label", func() {
			createTestExporter("exporter-match-test",
				map[string]string{"board": "test"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid: "dev1",
						Labels: map[string]string{
							"jumpstarter.dev/interface": "jumpstarter.interfaces.power.v1",
						},
					},
				},
			)

			createExporterClass("ec-match-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Selector: &metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "test"},
				},
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-match-test")

			ec := getExporterClass(ctx, "ec-match-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))
		})

		It("should not match when the exporter is missing a required interface", func() {
			createTestExporter("exporter-match-test",
				map[string]string{"board": "test"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)

			createExporterClass("ec-match-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Selector: &metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "test"},
				},
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power", Required: true},
					{Name: "serial", InterfaceRef: "di-serial", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-match-test")

			ec := getExporterClass(ctx, "ec-match-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(0))

			exporter := getExporterForTest(ctx, "exporter-match-test")
			Expect(exporter.Status.SatisfiedExporterClasses).NotTo(ContainElement("ec-match-test"))
			condition := meta.FindStatusCondition(exporter.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterConditionTypeExporterClassCompliance))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Status).To(Equal(metav1.ConditionFalse))
			Expect(condition.Reason).To(Equal("InterfaceMismatch"))
			Expect(condition.Message).To(ContainSubstring("serial"))
		})
	})

	Describe("Optional interface handling", func() {
		BeforeEach(func() {
			createDriverInterface("di-power-opt", "jumpstarter.interfaces.power.v1")
			createDriverInterface("di-network-opt", "jumpstarter.interfaces.network.v1")
		})

		AfterEach(func() {
			deleteDriverInterface("di-power-opt")
			deleteDriverInterface("di-network-opt")
			deleteExporterClass("ec-optional-test")
			deleteTestExporter("exporter-optional-test")
		})

		It("should satisfy the ExporterClass when optional interfaces are missing", func() {
			createTestExporter("exporter-optional-test",
				map[string]string{"board": "opt-test"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)

			createExporterClass("ec-optional-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Selector: &metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "opt-test"},
				},
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-opt", Required: true},
					{Name: "network", InterfaceRef: "di-network-opt", Required: false},
				},
			})

			reconcileExporterClass(ctx, "ec-optional-test")

			ec := getExporterClass(ctx, "ec-optional-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))
		})
	})

	Describe("ExporterClass inheritance", func() {
		BeforeEach(func() {
			createDriverInterface("di-power-inh", "jumpstarter.interfaces.power.v1")
			createDriverInterface("di-serial-inh", "jumpstarter.interfaces.serial.v1")
			createDriverInterface("di-storage-inh", "jumpstarter.interfaces.storage.v1")
		})

		AfterEach(func() {
			deleteDriverInterface("di-power-inh")
			deleteDriverInterface("di-serial-inh")
			deleteDriverInterface("di-storage-inh")
			deleteExporterClass("ec-parent")
			deleteExporterClass("ec-child")
			deleteExporterClass("ec-circular-a")
			deleteExporterClass("ec-circular-b")
			deleteTestExporter("exporter-inh-test")
		})

		It("should merge parent and child interface requirements", func() {
			// Parent requires power.
			createExporterClass("ec-parent", jumpstarterdevv1alpha1.ExporterClassSpec{
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-inh", Required: true},
				},
			})

			// Child extends parent, adds serial.
			createExporterClass("ec-child", jumpstarterdevv1alpha1.ExporterClassSpec{
				Extends: "ec-parent",
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "serial", InterfaceRef: "di-serial-inh", Required: true},
				},
			})

			// Exporter has both power and serial.
			createTestExporter("exporter-inh-test",
				map[string]string{},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
					{
						Uuid:                "dev2",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.serial.v1"),
					},
				},
			)

			reconcileExporterClass(ctx, "ec-child")

			ec := getExporterClass(ctx, "ec-child")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))
			Expect(ec.Status.ResolvedInterfaces).To(ContainElements("di-power-inh", "di-serial-inh"))
		})

		It("should allow child to override parent interface requirement", func() {
			// Parent requires power.
			createExporterClass("ec-parent", jumpstarterdevv1alpha1.ExporterClassSpec{
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-inh", Required: true},
				},
			})

			// Child overrides power to optional.
			createExporterClass("ec-child", jumpstarterdevv1alpha1.ExporterClassSpec{
				Extends: "ec-parent",
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-inh", Required: false},
				},
			})

			// Exporter has no interfaces.
			createTestExporter("exporter-inh-test",
				map[string]string{},
				[]jumpstarterdevv1alpha1.Device{},
			)

			reconcileExporterClass(ctx, "ec-child")

			ec := getExporterClass(ctx, "ec-child")
			// Power is now optional — exporter should satisfy.
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))
		})

		It("should detect circular extends and set Degraded condition", func() {
			createExporterClass("ec-circular-a", jumpstarterdevv1alpha1.ExporterClassSpec{
				Extends: "ec-circular-b",
			})
			createExporterClass("ec-circular-b", jumpstarterdevv1alpha1.ExporterClassSpec{
				Extends: "ec-circular-a",
			})

			reconcileExporterClass(ctx, "ec-circular-a")

			ec := getExporterClass(ctx, "ec-circular-a")
			degraded := meta.FindStatusCondition(ec.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterClassConditionTypeDegraded))
			Expect(degraded).NotTo(BeNil())
			Expect(degraded.Status).To(Equal(metav1.ConditionTrue))
			Expect(degraded.Reason).To(Equal("ResolutionFailed"))
			Expect(degraded.Message).To(ContainSubstring("circular"))

			ready := meta.FindStatusCondition(ec.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterClassConditionTypeReady))
			Expect(ready).NotTo(BeNil())
			Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		})
	})

	Describe("Label selector evaluation", func() {
		BeforeEach(func() {
			createDriverInterface("di-power-sel", "jumpstarter.interfaces.power.v1")
		})

		AfterEach(func() {
			deleteDriverInterface("di-power-sel")
			deleteExporterClass("ec-selector-test")
			deleteTestExporter("exporter-match-labels")
			deleteTestExporter("exporter-no-match-labels")
		})

		It("should only evaluate exporters matching the label selector", func() {
			// Matching exporter: has required interface.
			createTestExporter("exporter-match-labels",
				map[string]string{"vendor": "acme", "soc": "sa8295p"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)

			// Non-matching exporter: different labels (but also has the interface).
			createTestExporter("exporter-no-match-labels",
				map[string]string{"vendor": "other"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)

			createExporterClass("ec-selector-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Selector: &metav1.LabelSelector{
					MatchLabels: map[string]string{"vendor": "acme"},
				},
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-sel", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-selector-test")

			ec := getExporterClass(ctx, "ec-selector-test")
			// Only the matching exporter should be counted.
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))

			// The non-matching exporter should NOT have compliance conditions set.
			nonMatch := getExporterForTest(ctx, "exporter-no-match-labels")
			Expect(nonMatch.Status.SatisfiedExporterClasses).NotTo(ContainElement("ec-selector-test"))
		})

		It("should evaluate all exporters when no selector is set", func() {
			createTestExporter("exporter-match-labels",
				map[string]string{"vendor": "acme"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)
			createTestExporter("exporter-no-match-labels",
				map[string]string{"vendor": "other"},
				[]jumpstarterdevv1alpha1.Device{
					{
						Uuid:                "dev1",
						FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1"),
					},
				},
			)

			createExporterClass("ec-selector-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-sel", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-selector-test")

			ec := getExporterClass(ctx, "ec-selector-test")
			// Both exporters have the interface and no selector filter — both should satisfy.
			Expect(ec.Status.SatisfiedExporterCount).To(BeNumerically(">=", 2))
		})
	})

	Describe("Missing DriverInterface references", func() {
		AfterEach(func() {
			deleteExporterClass("ec-missing-di")
		})

		It("should set Degraded condition when a referenced DriverInterface does not exist", func() {
			createExporterClass("ec-missing-di", jumpstarterdevv1alpha1.ExporterClassSpec{
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-nonexistent", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-missing-di")

			ec := getExporterClass(ctx, "ec-missing-di")
			degraded := meta.FindStatusCondition(ec.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterClassConditionTypeDegraded))
			Expect(degraded).NotTo(BeNil())
			Expect(degraded.Status).To(Equal(metav1.ConditionTrue))
			Expect(degraded.Reason).To(Equal("MissingDriverInterface"))
			Expect(degraded.Message).To(ContainSubstring("di-nonexistent"))

			ready := meta.FindStatusCondition(ec.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterClassConditionTypeReady))
			Expect(ready).NotTo(BeNil())
			Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		})
	})

	Describe("SatisfiedExporterCount accuracy", func() {
		BeforeEach(func() {
			createDriverInterface("di-power-cnt", "jumpstarter.interfaces.power.v1")
		})

		AfterEach(func() {
			deleteDriverInterface("di-power-cnt")
			deleteExporterClass("ec-count-test")
			deleteTestExporter("exporter-cnt-1")
			deleteTestExporter("exporter-cnt-2")
			deleteTestExporter("exporter-cnt-3")
		})

		It("should accurately count satisfied exporters", func() {
			// Two compliant exporters.
			createTestExporter("exporter-cnt-1",
				map[string]string{"board": "cnt"},
				[]jumpstarterdevv1alpha1.Device{
					{Uuid: "d1", FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1")},
				},
			)
			createTestExporter("exporter-cnt-2",
				map[string]string{"board": "cnt"},
				[]jumpstarterdevv1alpha1.Device{
					{Uuid: "d2", FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1")},
				},
			)
			// One non-compliant exporter (no devices).
			createTestExporter("exporter-cnt-3",
				map[string]string{"board": "cnt"},
				[]jumpstarterdevv1alpha1.Device{},
			)

			createExporterClass("ec-count-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Selector: &metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "cnt"},
				},
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-cnt", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-count-test")

			ec := getExporterClass(ctx, "ec-count-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(2))
		})
	})

	Describe("Compliance condition updates on re-evaluation", func() {
		BeforeEach(func() {
			createDriverInterface("di-power-reeval", "jumpstarter.interfaces.power.v1")
			createDriverInterface("di-serial-reeval", "jumpstarter.interfaces.serial.v1")
		})

		AfterEach(func() {
			deleteDriverInterface("di-power-reeval")
			deleteDriverInterface("di-serial-reeval")
			deleteExporterClass("ec-reeval-test")
			deleteTestExporter("exporter-reeval")
		})

		It("should remove an exporter from satisfied list when ExporterClass gains a new required interface", func() {
			// Exporter only has power.
			createTestExporter("exporter-reeval",
				map[string]string{},
				[]jumpstarterdevv1alpha1.Device{
					{Uuid: "d1", FileDescriptorProto: makeFileDescriptorProto("jumpstarter.interfaces.power.v1")},
				},
			)

			// ExporterClass initially requires only power.
			createExporterClass("ec-reeval-test", jumpstarterdevv1alpha1.ExporterClassSpec{
				Interfaces: []jumpstarterdevv1alpha1.InterfaceRequirement{
					{Name: "power", InterfaceRef: "di-power-reeval", Required: true},
				},
			})

			reconcileExporterClass(ctx, "ec-reeval-test")

			ec := getExporterClass(ctx, "ec-reeval-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(1))

			exporter := getExporterForTest(ctx, "exporter-reeval")
			Expect(exporter.Status.SatisfiedExporterClasses).To(ContainElement("ec-reeval-test"))

			// Now update ExporterClass to also require serial.
			ec = getExporterClass(ctx, "ec-reeval-test")
			ec.Spec.Interfaces = []jumpstarterdevv1alpha1.InterfaceRequirement{
				{Name: "power", InterfaceRef: "di-power-reeval", Required: true},
				{Name: "serial", InterfaceRef: "di-serial-reeval", Required: true},
			}
			Expect(k8sClient.Update(ctx, ec)).To(Succeed())

			reconcileExporterClass(ctx, "ec-reeval-test")

			ec = getExporterClass(ctx, "ec-reeval-test")
			Expect(ec.Status.SatisfiedExporterCount).To(Equal(0))

			exporter = getExporterForTest(ctx, "exporter-reeval")
			Expect(exporter.Status.SatisfiedExporterClasses).NotTo(ContainElement("ec-reeval-test"))
		})
	})
})
