package v1alpha1

import (
	"strings"
	"testing"
	"time"

	cpb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/client/v1"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func FuzzParseLabelSelector(f *testing.F) {
	f.Add("app=myapp")
	f.Add("app=myapp,env=prod")
	f.Add("app = myapp , env = prod")
	f.Add("revision!=v3")
	f.Add("board-type=qc8775,revision!=v3")
	f.Add("revision!=v3,board-type!=qc8774")
	f.Add("env in (prod,staging)")
	f.Add("env notin (dev,test)")
	f.Add("app")
	f.Add("!app")
	f.Add("app=myapp,revision!=v3,env in (prod,staging),!debug")
	f.Add("")
	f.Add("version=v1.2.3,label=my-label")
	f.Add("board_type=qc8775,device_id=123")
	f.Add("invalid===syntax")
	f.Add("a=1,a=2")
	f.Add("a=1,a=1")
	f.Add("key!=value1,key!=value2")

	f.Fuzz(func(t *testing.T, input string) {
		sel, err := ParseLabelSelector(input)
		if err != nil {
			return
		}

		formatted := metav1.FormatLabelSelector(sel)

		if formatted == "<none>" {
			return
		}

		sel2, err := ParseLabelSelector(formatted)
		if err != nil {
			t.Errorf("round-trip failed: ParseLabelSelector(%q) succeeded, formatted to %q, but re-parse failed: %v", input, formatted, err)
			return
		}

		formatted2 := metav1.FormatLabelSelector(sel2)
		if formatted != formatted2 {
			t.Errorf("round-trip not stable: format(%q) = %q, but format(parse(%q)) = %q", input, formatted, formatted, formatted2)
		}
	})
}

func FuzzReconcileLeaseTimeFields(f *testing.F) {
	f.Add(int64(0), int64(3600), int64(3600))
	f.Add(int64(1000), int64(2000), int64(1000))
	f.Add(int64(0), int64(0), int64(100))
	f.Add(int64(-1), int64(0), int64(0))
	f.Add(int64(0), int64(0), int64(0))
	f.Add(int64(0), int64(0), int64(-1))

	f.Fuzz(func(t *testing.T, beginSec, endSec, durSec int64) {
		var beginTime, endTime *metav1.Time
		var duration *metav1.Duration

		if beginSec != 0 {
			bt := metav1.NewTime(time.Unix(beginSec, 0))
			beginTime = &bt
		}
		if endSec != 0 {
			et := metav1.NewTime(time.Unix(endSec, 0))
			endTime = &et
		}
		if durSec != 0 {
			duration = &metav1.Duration{Duration: time.Duration(durSec) * time.Second}
		}

		err := ReconcileLeaseTimeFields(&beginTime, &endTime, &duration)
		if err != nil {
			return
		}

		if duration == nil {
			t.Errorf("ReconcileLeaseTimeFields succeeded but duration is nil")
			return
		}
		if duration.Duration <= 0 {
			t.Errorf("ReconcileLeaseTimeFields succeeded but duration is non-positive: %v", duration.Duration)
		}

		if beginTime != nil && endTime != nil {
			calculated := endTime.Sub(beginTime.Time)
			if duration.Duration != calculated {
				t.Errorf("time fields inconsistent: endTime - beginTime = %v but duration = %v", calculated, duration.Duration)
			}
		}
	})
}

func FuzzValidateLeaseTags(f *testing.F) {
	f.Add("team", "devops", 10)
	f.Add("ci-job", "12345", 10)
	f.Add("jumpstarter.dev/custom", "value", 10)
	f.Add("metadata.jumpstarter.dev/team", "value", 10)
	f.Add("team/env", "value", 10)
	f.Add("a", "value", 0)
	f.Add("", "value", 10)
	f.Add("valid-key", "", 10)

	f.Fuzz(func(t *testing.T, key, value string, maxTags int) {
		if maxTags < 0 {
			maxTags = 0
		}
		if maxTags > 100 {
			maxTags = 100
		}
		tags := map[string]string{key: value}
		err := ValidateLeaseTags(tags, maxTags)

		if maxTags == 0 && len(tags) > 0 {
			if err == nil {
				t.Errorf("ValidateLeaseTags accepted tags when maxTags=0")
			}
			return
		}

		if strings.HasPrefix(key, LeaseTagMetadataPrefix) {
			if err == nil {
				t.Errorf("ValidateLeaseTags accepted metadata prefix key %q", key)
			}
			return
		}

		if strings.HasPrefix(key, "jumpstarter.dev/") {
			if err == nil {
				t.Errorf("ValidateLeaseTags accepted jumpstarter.dev/ prefix key %q", key)
			}
			return
		}

		if strings.Contains(key, "/") {
			if err == nil {
				t.Errorf("ValidateLeaseTags accepted key with slash: %q", key)
			}
			return
		}
	})
}

func FuzzLeaseFromProtobuf(f *testing.F) {
	f.Add("dut=a", int64(3600), int64(0), int64(0), "", false, "team", "devops")
	f.Add("env=prod", int64(60), int64(1000), int64(0), "my-exporter", true, "", "")
	f.Add("", int64(300), int64(0), int64(0), "exp1", true, "", "")
	f.Add("app=test", int64(0), int64(1000), int64(2000), "", false, "", "")

	f.Fuzz(func(t *testing.T, selector string, durSec, beginSec, endSec int64, exporterName string, hasExporter bool, tagKey, tagValue string) {
		req := &cpb.Lease{Selector: selector}

		if durSec > 0 {
			req.Duration = durationpb.New(time.Duration(durSec) * time.Second)
		}
		if beginSec > 0 {
			req.BeginTime = timestamppb.New(time.Unix(beginSec, 0))
		}
		if endSec > 0 {
			req.EndTime = timestamppb.New(time.Unix(endSec, 0))
		}
		if hasExporter {
			req.ExporterName = &exporterName
		}
		if tagKey != "" && tagValue != "" {
			req.Tags = map[string]string{tagKey: tagValue}
		}

		key := types.NamespacedName{Namespace: "default", Name: "test-lease"}
		clientRef := corev1.LocalObjectReference{Name: "test-client"}

		lease, err := LeaseFromProtobuf(req, key, clientRef)
		if err != nil {
			return
		}

		if lease.Namespace != key.Namespace {
			t.Errorf("lease namespace = %q, expected %q", lease.Namespace, key.Namespace)
		}
		if lease.Name != key.Name {
			t.Errorf("lease name = %q, expected %q", lease.Name, key.Name)
		}
		if lease.Spec.ClientRef.Name != clientRef.Name {
			t.Errorf("client ref = %q, expected %q", lease.Spec.ClientRef.Name, clientRef.Name)
		}

		if lease.Spec.Duration == nil || lease.Spec.Duration.Duration <= 0 {
			t.Errorf("lease duration should be positive after successful LeaseFromProtobuf, got %v", lease.Spec.Duration)
		}

		if hasExporter && exporterName != "" {
			if lease.Spec.ExporterRef == nil {
				t.Error("expected ExporterRef to be set")
			} else if lease.Spec.ExporterRef.Name != exporterName {
				t.Errorf("ExporterRef.Name = %q, expected %q", lease.Spec.ExporterRef.Name, exporterName)
			}
		}
	})
}

func FuzzLeaseToProtobuf(f *testing.F) {
	f.Add("test-lease", "default", "test-client", int64(3600), "exp1", true)
	f.Add("lease", "ns", "client", int64(60), "", false)

	f.Fuzz(func(t *testing.T, name, namespace, clientName string, durSec int64, exporterName string, hasExporter bool) {
		lease := &Lease{
			ObjectMeta: metav1.ObjectMeta{
				Name:      name,
				Namespace: namespace,
			},
			Spec: LeaseSpec{
				ClientRef: corev1.LocalObjectReference{Name: clientName},
				Selector:  metav1.LabelSelector{},
			},
		}

		if durSec > 0 {
			lease.Spec.Duration = &metav1.Duration{Duration: time.Duration(durSec) * time.Second}
		}
		if hasExporter {
			lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: exporterName}
		}

		pb := lease.ToProtobuf()

		expectedName := "namespaces/" + namespace + "/leases/" + name
		if pb.Name != expectedName {
			t.Errorf("protobuf Name = %q, expected %q", pb.Name, expectedName)
		}

		expectedClient := "namespaces/" + namespace + "/clients/" + clientName
		if pb.Client == nil || *pb.Client != expectedClient {
			t.Errorf("protobuf Client = %v, expected %q", pb.Client, expectedClient)
		}

		if hasExporter && exporterName != "" {
			if pb.ExporterName == nil || *pb.ExporterName != exporterName {
				t.Errorf("protobuf ExporterName = %v, expected %q", pb.ExporterName, exporterName)
			}
		}

		if durSec > 0 {
			if pb.Duration == nil {
				t.Error("expected protobuf Duration to be set")
			}
		}
	})
}

func FuzzLeaseGetExporterSelector(f *testing.F) {
	f.Add("app", "myapp")
	f.Add("env", "prod")
	f.Add("", "")

	f.Fuzz(func(t *testing.T, labelKey, labelValue string) {
		lease := &Lease{
			Spec: LeaseSpec{
				Selector: metav1.LabelSelector{
					MatchLabels: map[string]string{labelKey: labelValue},
				},
			},
		}

		sel, err := lease.GetExporterSelector()
		if err != nil {
			return
		}
		if sel == nil {
			t.Error("GetExporterSelector returned nil selector without error")
		}
	})
}

func FuzzLeaseListToProtobuf(f *testing.F) {
	f.Add("lease1", "lease2", "default", "client1")

	f.Fuzz(func(t *testing.T, name1, name2, namespace, clientName string) {
		list := &LeaseList{
			Items: []Lease{
				{
					ObjectMeta: metav1.ObjectMeta{Name: name1, Namespace: namespace},
					Spec:       LeaseSpec{ClientRef: corev1.LocalObjectReference{Name: clientName}},
				},
				{
					ObjectMeta: metav1.ObjectMeta{Name: name2, Namespace: namespace},
					Spec:       LeaseSpec{ClientRef: corev1.LocalObjectReference{Name: clientName}},
				},
			},
		}

		resp := list.ToProtobuf()

		if len(resp.Leases) != 2 {
			t.Fatalf("expected 2 leases in protobuf response, got %d", len(resp.Leases))
		}
	})
}
