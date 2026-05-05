/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package v1

import (
	"context"
	"time"

	adminv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/watch"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// EventEnvelope is the shared, untyped event shape passed to per-resource
// stream emit callbacks. The caller down-casts ev.Object to the concrete
// CRD type (Lease/Exporter/Client/Webhook) it knows about.
type EventEnvelope struct {
	Type   adminv1.EventType
	Object runtime.Object
}

// bookmarkInterval is how often an idle Watch stream emits a BOOKMARK
// record. The choice tracks the JEP's 30s recommendation, which is well
// under typical HTTP idle timeouts and matches Kubernetes informer
// bookmark cadence.
const bookmarkInterval = 30 * time.Second

// runWatch translates a controller-runtime kclient.WithWatch list-watch on
// the supplied list type into a stream of admin.v1 typed events via the
// emit callback. It enforces the resource_version resume contract and
// emits BOOKMARK records every 30s on otherwise-idle streams.
//
// On a 410 Gone (resource version too old / informer cache evicted),
// runWatch returns codes.OutOfRange so the client knows it must re-list
// from scratch.
func runWatch(
	ctx context.Context,
	w kclient.WithWatch,
	namespace string,
	resumeRV string,
	selector labels.Selector,
	listType kclient.ObjectList,
	emit func(rv string, ev EventEnvelope) error,
) error {
	opts := []kclient.ListOption{kclient.InNamespace(namespace)}
	if selector != nil && !selector.Empty() {
		opts = append(opts, kclient.MatchingLabelsSelector{Selector: selector})
	}

	wi, err := w.Watch(ctx, listType, opts...)
	if err != nil {
		return kerr(err)
	}
	defer wi.Stop()

	tick := time.NewTicker(bookmarkInterval)
	defer tick.Stop()

	lastRV := resumeRV
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-tick.C:
			// BOOKMARK keeps idle HTTP/1.1 chunked transfers warm and
			// gives clients a resumable cursor.
			if err := emit(lastRV, EventEnvelope{Type: adminv1.EventType_EVENT_TYPE_BOOKMARK}); err != nil {
				return err
			}
		case ev, ok := <-wi.ResultChan():
			if !ok {
				return nil
			}
			switch ev.Type {
			case watch.Error:
				if statusErr, ok := ev.Object.(interface{ Status() error }); ok {
					if k8sIsGone(statusErr.Status()) {
						return status.Errorf(codes.OutOfRange, "watch resourceVersion %q expired; re-list and resume", lastRV)
					}
				}
				return status.Errorf(codes.Internal, "watch error: %v", ev.Object)
			case watch.Bookmark:
				if obj, ok := ev.Object.(kclient.Object); ok {
					lastRV = obj.GetResourceVersion()
				}
				if err := emit(lastRV, EventEnvelope{Type: adminv1.EventType_EVENT_TYPE_BOOKMARK}); err != nil {
					return err
				}
			default:
				obj, ok := ev.Object.(kclient.Object)
				if !ok {
					continue
				}
				lastRV = obj.GetResourceVersion()
				kind := watchTypeToProto(ev.Type)
				if kind == adminv1.EventType_EVENT_TYPE_UNSPECIFIED {
					continue
				}
				if err := emit(lastRV, EventEnvelope{Type: kind, Object: ev.Object}); err != nil {
					return err
				}
				// Reset the bookmark timer after every real event.
				tick.Reset(bookmarkInterval)
			}
		}
	}
}

func watchTypeToProto(t watch.EventType) adminv1.EventType {
	switch t {
	case watch.Added:
		return adminv1.EventType_EVENT_TYPE_ADDED
	case watch.Modified:
		return adminv1.EventType_EVENT_TYPE_MODIFIED
	case watch.Deleted:
		return adminv1.EventType_EVENT_TYPE_DELETED
	default:
		return adminv1.EventType_EVENT_TYPE_UNSPECIFIED
	}
}
