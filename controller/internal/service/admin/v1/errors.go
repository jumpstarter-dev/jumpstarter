/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package v1

import (
	apierrors "k8s.io/apimachinery/pkg/api/errors"
)

// Trampolines around apimachinery error predicates so lease_service.go
// stays focused on flow rather than dragging k8s.io/apimachinery imports
// across every handler. They forward verbatim.
func k8sIsNotFound(err error) bool      { return apierrors.IsNotFound(err) }
func k8sIsAlreadyExists(err error) bool { return apierrors.IsAlreadyExists(err) }
func k8sIsForbidden(err error) bool     { return apierrors.IsForbidden(err) }
func k8sIsInvalid(err error) bool       { return apierrors.IsInvalid(err) }
func k8sIsConflict(err error) bool      { return apierrors.IsConflict(err) }
func k8sIsGone(err error) bool          { return apierrors.IsGone(err) }
