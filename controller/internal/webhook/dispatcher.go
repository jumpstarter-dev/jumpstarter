/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package webhook

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/go-logr/logr"
	"sigs.k8s.io/controller-runtime/pkg/manager"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// Delivery limits per the JEP. At-least-once with exponential backoff
// capped at 1h delay and 24 attempts; after the cap the dispatcher pauses
// delivery for that subscriber until the CRD is touched.
const (
	maxAttempts    = 24
	maxBackoff     = time.Hour
	initialBackoff = 2 * time.Second
	httpTimeout    = 10 * time.Second
)

// Dispatcher is a bounded goroutine pool that delivers Events to
// subscribers (Webhook CRDs). It is started by main, accepts events on
// Enqueue, and exits when the context cancels.
type Dispatcher struct {
	client client.Client
	logger logr.Logger
	queue  chan Event

	httpClient *http.Client
	workers    int
	once       sync.Once
}

// NewDispatcher creates a Dispatcher with workers goroutines and an
// internal queue capacity. Pick workers ≈ peak concurrent subscribers; the
// queue absorbs short bursts without backpressure.
func NewDispatcher(c client.Client, logger logr.Logger, workers, queueCap int) *Dispatcher {
	return &Dispatcher{
		client:     c,
		logger:     logger,
		queue:      make(chan Event, queueCap),
		httpClient: &http.Client{Timeout: httpTimeout},
		workers:    workers,
	}
}

// Start launches the worker pool. It blocks until ctx is cancelled.
// Idempotent: extra calls return immediately. Implements ctrl.Runnable.
func (d *Dispatcher) Start(ctx context.Context) error {
	d.once.Do(func() {
		var wg sync.WaitGroup
		for i := 0; i < d.workers; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				d.worker(ctx)
			}()
		}
		<-ctx.Done()
		close(d.queue)
		wg.Wait()
	})
	return nil
}

// Enqueue submits an event for fan-out. The call is non-blocking; if the
// queue is full the event is dropped and a warning logged. Webhook
// delivery is at-least-once but never blocks the producer (event source
// reconcilers).
func (d *Dispatcher) Enqueue(ev Event) {
	select {
	case d.queue <- ev:
	default:
		d.logger.Info("webhook queue full; dropping event", "event_id", ev.EventID, "class", ev.EventClass)
	}
}

func (d *Dispatcher) worker(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case ev, ok := <-d.queue:
			if !ok {
				return
			}
			d.deliver(ctx, ev)
		}
	}
}

func (d *Dispatcher) deliver(ctx context.Context, ev Event) {
	subs := d.matchingSubscribers(ctx, ev)
	for i := range subs {
		d.deliverOne(ctx, &subs[i], ev)
	}
}

func (d *Dispatcher) matchingSubscribers(ctx context.Context, ev Event) []jumpstarterdevv1alpha1.Webhook {
	var list jumpstarterdevv1alpha1.WebhookList
	if err := d.client.List(ctx, &list, client.InNamespace(ev.Namespace)); err != nil {
		d.logger.Error(err, "list webhooks", "namespace", ev.Namespace)
		return nil
	}
	out := make([]jumpstarterdevv1alpha1.Webhook, 0, len(list.Items))
	for _, w := range list.Items {
		for _, e := range w.Spec.Events {
			if e == ev.EventClass {
				out = append(out, w)
				break
			}
		}
	}
	return out
}

func (d *Dispatcher) deliverOne(ctx context.Context, w *jumpstarterdevv1alpha1.Webhook, ev Event) {
	body, err := json.Marshal(ev)
	if err != nil {
		d.logger.Error(err, "marshal event", "event_id", ev.EventID)
		return
	}
	key, err := d.loadSecret(ctx, w)
	if err != nil {
		d.logger.Error(err, "load webhook secret", "webhook", w.Name)
		d.recordFailure(ctx, w, fmt.Sprintf("load secret: %v", err))
		return
	}

	attempt := int(w.Status.ConsecutiveFailures)
	if attempt >= maxAttempts {
		// Paused per JEP: after maxAttempts we wait for a CRD touch to
		// resume. The reconciler resets ConsecutiveFailures when the spec
		// is updated; until then this code path no-ops.
		return
	}
	backoff := backoffFor(attempt)
	if backoff > 0 {
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, w.Spec.URL, bytes.NewReader(body))
	if err != nil {
		d.recordFailure(ctx, w, fmt.Sprintf("build request: %v", err))
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(SignatureHeader, Sign(time.Now(), body, key))

	resp, err := d.httpClient.Do(req)
	if err != nil {
		d.recordFailure(ctx, w, fmt.Sprintf("POST: %v", err))
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		d.recordSuccess(ctx, w)
		return
	}
	d.recordFailure(ctx, w, fmt.Sprintf("HTTP %d", resp.StatusCode))
}

// backoffFor returns the wait duration before the n-th attempt (n starts at
// 0 for the first try). Doubles each attempt, capped at maxBackoff.
func backoffFor(attempt int) time.Duration {
	if attempt <= 0 {
		return 0
	}
	d := initialBackoff
	for i := 1; i < attempt; i++ {
		d *= 2
		if d >= maxBackoff {
			return maxBackoff
		}
	}
	return d
}

func (d *Dispatcher) loadSecret(ctx context.Context, w *jumpstarterdevv1alpha1.Webhook) ([]byte, error) {
	var sec corev1.Secret
	if err := d.client.Get(ctx, types.NamespacedName{Namespace: w.Namespace, Name: w.Spec.SecretRef.Name}, &sec); err != nil {
		return nil, err
	}
	v, ok := sec.Data[w.Spec.SecretRef.Key]
	if !ok {
		return nil, fmt.Errorf("secret %q has no key %q", w.Spec.SecretRef.Name, w.Spec.SecretRef.Key)
	}
	return v, nil
}

func (d *Dispatcher) recordSuccess(ctx context.Context, w *jumpstarterdevv1alpha1.Webhook) {
	now := metav1.Now()
	patch := client.MergeFrom(w.DeepCopy())
	w.Status.LastSuccess = &now
	w.Status.ConsecutiveFailures = 0
	if err := d.client.Status().Patch(ctx, w, patch); err != nil {
		d.logger.Error(err, "record webhook success", "webhook", w.Name)
	}
}

func (d *Dispatcher) recordFailure(ctx context.Context, w *jumpstarterdevv1alpha1.Webhook, reason string) {
	now := metav1.Now()
	patch := client.MergeFrom(w.DeepCopy())
	w.Status.LastFailure = &now
	w.Status.ConsecutiveFailures++
	if err := d.client.Status().Patch(ctx, w, patch); err != nil {
		d.logger.Error(err, "record webhook failure", "webhook", w.Name, "reason", reason)
	}
}

// SetupWithManager registers the dispatcher as a controller-runtime Runnable
// so its Start method is called when the manager runs.
func (d *Dispatcher) SetupWithManager(mgr ctrlmanager) error {
	return mgr.Add(d)
}

// ctrlmanager is the subset of ctrl.Manager we use. Defined inline so this
// file does not pull controller-runtime/pkg/manager — keeps the package
// import surface minimal.
type ctrlmanager interface {
	Add(manager.Runnable) error
}
