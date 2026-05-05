/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package webhook

import (
	"time"

	"github.com/google/uuid"
)

// Event is the canonical envelope POSTed to webhook subscribers.
//
// EventID is a UUIDv7 generated at first emission. Consumers MUST
// deduplicate on it: at-least-once delivery means the dispatcher may
// re-send the same EventID after a transient failure.
type Event struct {
	EventID    string         `json:"event_id"`
	EventClass string         `json:"event_class"`
	OccurredAt time.Time      `json:"occurred_at"`
	Namespace  string         `json:"namespace"`
	Object     map[string]any `json:"object"`
}

// NewEvent constructs an Event with a freshly generated EventID and
// OccurredAt = time.Now(). The class string MUST match the WebhookEvent*
// constants in api/v1alpha1/webhook_types.go.
func NewEvent(class, namespace string, object map[string]any) Event {
	id, err := uuid.NewV7()
	if err != nil {
		// Fallback to UUIDv4 if v7 fails (clock skew, mostly). Never blocks.
		id = uuid.New()
	}
	return Event{
		EventID:    id.String(),
		EventClass: class,
		OccurredAt: time.Now().UTC(),
		Namespace:  namespace,
		Object:     object,
	}
}
