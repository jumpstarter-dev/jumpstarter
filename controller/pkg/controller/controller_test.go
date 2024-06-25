package controller

import (
	"log"
	"testing"

	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

func TestController(t *testing.T) {
	env := &envtest.Environment{}

	cfg, err := env.Start()
	if err != nil {
		t.Fatalf("failed to start envtest: %s", err)
	}

	log.Println(cfg)

	env.Stop()
}
