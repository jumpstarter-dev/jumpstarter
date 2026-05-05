/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package webhook implements the controller's outbound webhook delivery
// pipeline. Events emitted by the existing reconcilers are turned into
// signed HTTPS POSTs to subscribers configured via the Webhook CRD.
package webhook

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strconv"
	"time"
)

// SignatureHeader is the canonical header name webhook subscribers verify.
// The format mirrors Stripe's signature header so consumers can reuse
// battle-tested verification code:
//
//	X-Jumpstarter-Signature: t=<unix>,v1=<hmac-sha256>
//
// where the signed payload is "<unix>.<json-body>".
const SignatureHeader = "X-Jumpstarter-Signature"

// Sign returns the SignatureHeader value for body and key at time t.
func Sign(t time.Time, body []byte, key []byte) string {
	ts := strconv.FormatInt(t.Unix(), 10)
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(ts))
	mac.Write([]byte("."))
	mac.Write(body)
	return fmt.Sprintf("t=%s,v1=%s", ts, hex.EncodeToString(mac.Sum(nil)))
}
