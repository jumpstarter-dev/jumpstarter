package controller

import (
	"github.com/go-logr/logr"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	ctrl "sigs.k8s.io/controller-runtime"
)

func RequeueConflict(logger logr.Logger, result ctrl.Result, err error) (ctrl.Result, error) {
	if apierrors.IsConflict(err) {
		logger.V(1).Info("Ignoring conflict error but requeuing the reconciliation request", "error", err)
		return ctrl.Result{Requeue: true}, nil
	} else {
		return result, err
	}
}
