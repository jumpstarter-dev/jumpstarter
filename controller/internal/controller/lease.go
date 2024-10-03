package controller

import (
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/selection"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
)

func MatchingActiveLeases() client.ListOption {
	// TODO: use field selector once KEP-4358 is stabilized
	// Reference: https://github.com/kubernetes/kubernetes/pull/122717
	requirement, err := labels.NewRequirement(
		string(jumpstarterdevv1alpha1.LeaseLabelEnded),
		selection.DoesNotExist,
		[]string{},
	)

	utilruntime.Must(err)

	return client.MatchingLabelsSelector{
		Selector: labels.Everything().Add(*requirement),
	}
}
