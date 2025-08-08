package v1alpha1

import (
	"strings"

	cpb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/client/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/utils"
	"k8s.io/apimachinery/pkg/api/meta"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

func (e *Exporter) InternalSubject() string {
	return strings.Join([]string{"exporter", e.Namespace, e.Name, string(e.UID)}, ":")
}

func (e *Exporter) Usernames(prefix string) []string {
	usernames := []string{prefix + e.InternalSubject()}

	if e.Spec.Username != nil {
		usernames = append(usernames, *e.Spec.Username)
	}

	return usernames
}

func (e *Exporter) ToProtobuf() *cpb.Exporter {
	// get online status from conditions
	isOnline := meta.IsStatusConditionTrue(e.Status.Conditions, string(ExporterConditionTypeOnline))

	return &cpb.Exporter{
		Name:   utils.UnparseExporterIdentifier(kclient.ObjectKeyFromObject(e)),
		Labels: e.Labels,
		Online: isOnline,
	}
}

func (l *ExporterList) ToProtobuf() *cpb.ListExportersResponse {
	var jexporters []*cpb.Exporter
	for _, jexporter := range l.Items {
		jexporters = append(jexporters, jexporter.ToProtobuf())
	}
	return &cpb.ListExportersResponse{
		Exporters:     jexporters,
		NextPageToken: l.Continue,
	}
}
