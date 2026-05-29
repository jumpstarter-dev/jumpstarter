package v1alpha1

import (
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
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
		// ParseLabelSelector must not panic on any input.
		// Returning an error is acceptable.
		_, _ = ParseLabelSelector(input)
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

		// ReconcileLeaseTimeFields must not panic.
		_ = ReconcileLeaseTimeFields(&beginTime, &endTime, &duration)
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
		// ValidateLeaseTags must not panic.
		_ = ValidateLeaseTags(tags, maxTags)
	})
}
