/*
Copyright 2026 The Jumpstarter Authors

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

package qemussh

import (
	"encoding/json"
	"errors"
	"fmt"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
)

// ConnectionConfig captures everything needed to reach an exporter's remote
// host over SSH: the host endpoint (embedded HostConfig) plus the credentials
// Secret name. Deploy records it on the Exporter CR before any remote
// containers exist; Cleanup reads it back verbatim. Teardown therefore
// reconnects with the exact details used to deploy, even if the
// VirtualTargetClass or ExporterSet parameters change in between — which
// previously caused Cleanup to SSH with the wrong user/port and silently orphan
// containers.
type ConnectionConfig struct {
	HostConfig
	Secret string `json:"secret"`
}

// validate reports whether every field needed to reconnect is set.
func (c ConnectionConfig) validate() error {
	switch {
	case c.Host == "":
		return errors.New("missing host")
	case c.Port == 0:
		return errors.New("missing port")
	case c.User == "":
		return errors.New("missing user")
	case c.Secret == "":
		return errors.New("missing credentials secret")
	}
	return nil
}

// connectionFromExporter returns the connection recorded on the Exporter.
//
// present is false when there is no connection annotation, meaning Deploy never
// ran and there is nothing to clean up. When the annotation exists, present is
// true; err is non-nil if it is malformed or missing a required field, so the
// caller can tell a corrupt record (resources may exist) from an absent one.
func connectionFromExporter(exporter *jumpstarterdevv1alpha1.Exporter) (ConnectionConfig, bool, error) {
	if exporter.Annotations == nil {
		return ConnectionConfig{}, false, nil
	}

	raw, ok := exporter.Annotations[AnnotationConnection]
	if !ok {
		return ConnectionConfig{}, false, nil
	}

	var c ConnectionConfig
	if err := json.Unmarshal([]byte(raw), &c); err != nil {
		return ConnectionConfig{}, true, fmt.Errorf("parse connection annotation: %w", err)
	}
	if err := c.validate(); err != nil {
		return c, true, fmt.Errorf("connection annotation invalid: %w", err)
	}
	return c, true, nil
}
