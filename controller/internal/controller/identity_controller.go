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
	"fmt"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/uuid"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"

	"github.com/golang-jwt/jwt/v5"
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
)

// IdentityReconciler reconciles a Identity object
type IdentityReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=jumpstarter.dev,resources=identities,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=identities/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=identities/finalizers,verbs=update

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
// TODO(user): Modify the Reconcile function to compare the state specified by
// the Identity object against the actual cluster state, and then
// perform operations to make the cluster state reflect the state specified by
// the user.
//
// For more details, check Reconcile and its Result here:
// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.18.2/pkg/reconcile
func (r *IdentityReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	client := &jumpstarterdevv1alpha1.Client{}
	err := r.Get(ctx, req.NamespacedName, client)
	if apierrors.IsNotFound(err) {
		logger.Info("reconcile: Client deleted", "client", req.NamespacedName)
		// Request object not found, could have been deleted after reconcile request.
		// Owned objects are automatically garbage collected. For additional cleanup logic use finalizers.
		return ctrl.Result{}, nil
	}

	if err != nil {
		logger.Error(err, "reconcile: unable to fetch Client")
		return ctrl.Result{}, err
	}

	if client.Status.Credential == nil {
		logger.Info("reconcile: Client has no credentials, creating credentials", "client", req.NamespacedName)
		secret, err := r.secretForClient(client)
		if err != nil {
			logger.Error(err, "reconcile: unable to create secret for Client")
			return ctrl.Result{}, err
		}
		err = r.Create(ctx, secret)
		if err != nil {
			logger.Error(err, "reconcile: unable to create secret for Client", "exporter", req.NamespacedName, "secret", secret.GetName())
			return ctrl.Result{}, err
		}
		client.Status.Credential = &corev1.LocalObjectReference{
			Name: secret.Name,
		}
		err = r.Status().Update(ctx, client)
		if err != nil {
			logger.Error(err, "reconcile: unable to update Client with secret reference", "exporter", req.NamespacedName, "secret", secret.GetName())
			return ctrl.Result{}, err
		}
	}

	return ctrl.Result{}, nil
}

func (r *IdentityReconciler) secretForClient(client *jumpstarterdevv1alpha1.Client) (*corev1.Secret, error) {
	token, err := SignObjectToken(
		"https://jumpstarter.dev/controller",
		[]string{"https://jumpstarter.dev/controller"},
		jwt.SigningMethodHS256,
		[]byte(""),
		client,
		r.Scheme,
	)
	if err != nil {
		return nil, err
	}

	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      client.Name + "-token",
			Namespace: client.Namespace,
		},
		Type: corev1.SecretTypeOpaque,
		StringData: map[string]string{
			"token": token,
		},
	}
	// enable garbage collection on the created resource
	if err := controllerutil.SetOwnerReference(client, secret, r.Scheme); err != nil {
		return nil, fmt.Errorf("secretForClient, error setting owner reference: %w", err)
	}
	return secret, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *IdentityReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Client{}).
		Complete(r)
}
