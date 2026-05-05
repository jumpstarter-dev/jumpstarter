/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package controller

import (
	"context"
	"sync"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/webhook"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// WebhookEventEmitter holds the per-resource transition caches that turn
// kube events into webhook deliveries. Two reconcilers (one for Leases,
// one for Exporters) share an emitter so transition tracking is namespaced
// per resource kind.
type WebhookEventEmitter struct {
	Dispatcher *webhook.Dispatcher

	mu                sync.Mutex
	leaseEnded        map[string]bool
	exporterStatusVal map[string]string
}

// LeaseWebhookReconciler emits LeaseCreated/LeaseEnded webhook events on
// Lease state transitions.
type LeaseWebhookReconciler struct {
	client.Client
	Scheme  *runtime.Scheme
	Emitter *WebhookEventEmitter
}

func (r *LeaseWebhookReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	var lease jumpstarterdevv1alpha1.Lease
	if err := r.Get(ctx, req.NamespacedName, &lease); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	r.Emitter.observeLease(&lease)
	return ctrl.Result{}, nil
}

func (r *LeaseWebhookReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		Named("lease-webhook").
		For(&jumpstarterdevv1alpha1.Lease{}).
		Complete(r)
}

// ExporterWebhookReconciler emits ExporterOffline/ExporterAvailable on
// Exporter status transitions.
type ExporterWebhookReconciler struct {
	client.Client
	Scheme  *runtime.Scheme
	Emitter *WebhookEventEmitter
}

func (r *ExporterWebhookReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	var e jumpstarterdevv1alpha1.Exporter
	if err := r.Get(ctx, req.NamespacedName, &e); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}
	r.Emitter.observeExporter(&e)
	return ctrl.Result{}, nil
}

func (r *ExporterWebhookReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		Named("exporter-webhook").
		For(&jumpstarterdevv1alpha1.Exporter{}).
		Complete(r)
}

// observeLease emits LeaseCreated on first observation and LeaseEnded on
// the false→true Status.Ended transition. Idempotent under reconcile
// re-runs because the cache prevents duplicate emissions.
func (e *WebhookEventEmitter) observeLease(l *jumpstarterdevv1alpha1.Lease) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.leaseEnded == nil {
		e.leaseEnded = map[string]bool{}
	}
	key := l.Namespace + "/" + l.Name
	prev, seen := e.leaseEnded[key]
	if !seen {
		e.Dispatcher.Enqueue(webhook.NewEvent(
			jumpstarterdevv1alpha1.WebhookEventLeaseCreated,
			l.Namespace,
			map[string]any{
				"name":      l.Name,
				"namespace": l.Namespace,
				"client":    l.Spec.ClientRef.Name,
				"ended":     l.Status.Ended,
			},
		))
	}
	if l.Status.Ended && !prev {
		e.Dispatcher.Enqueue(webhook.NewEvent(
			jumpstarterdevv1alpha1.WebhookEventLeaseEnded,
			l.Namespace,
			map[string]any{
				"name":      l.Name,
				"namespace": l.Namespace,
				"client":    l.Spec.ClientRef.Name,
			},
		))
	}
	e.leaseEnded[key] = l.Status.Ended
}

func (e *WebhookEventEmitter) observeExporter(x *jumpstarterdevv1alpha1.Exporter) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.exporterStatusVal == nil {
		e.exporterStatusVal = map[string]string{}
	}
	key := x.Namespace + "/" + x.Name
	prev := e.exporterStatusVal[key]
	cur := x.Status.ExporterStatusValue
	if cur != prev {
		switch cur {
		case jumpstarterdevv1alpha1.ExporterStatusOffline:
			e.Dispatcher.Enqueue(webhook.NewEvent(
				jumpstarterdevv1alpha1.WebhookEventExporterOffline,
				x.Namespace,
				map[string]any{"name": x.Name, "namespace": x.Namespace, "status": cur},
			))
		case jumpstarterdevv1alpha1.ExporterStatusAvailable:
			e.Dispatcher.Enqueue(webhook.NewEvent(
				jumpstarterdevv1alpha1.WebhookEventExporterAvailable,
				x.Namespace,
				map[string]any{"name": x.Name, "namespace": x.Namespace, "status": cur},
			))
		}
	}
	e.exporterStatusVal[key] = cur
}
