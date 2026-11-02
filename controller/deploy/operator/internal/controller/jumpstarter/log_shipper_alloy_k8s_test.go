/*
Copyright 2026.

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

package jumpstarter

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

// TestAlloyKubernetesLiveDelivery validates the Kubernetes discovery and pod-log
// tailing path with a host-run Alloy process and the current Kind kubeconfig.
// It is gated because it needs a live Kubernetes cluster and the Alloy binary.
func TestAlloyKubernetesLiveDelivery(t *testing.T) {
	if os.Getenv("ALLOY_K8S_E2E") != "1" {
		t.Skip("set ALLOY_K8S_E2E=1 to run the Kubernetes Alloy delivery test")
	}
	alloyBin := os.Getenv("ALLOY_BIN")
	if alloyBin == "" {
		if path, err := exec.LookPath("alloy"); err == nil {
			alloyBin = path
		} else {
			t.Skip("set ALLOY_BIN or install alloy to run the Kubernetes Alloy delivery test")
		}
	}
	if _, err := os.Stat(alloyBin); err != nil {
		t.Skipf("Alloy binary %s is not available: %v", alloyBin, err)
	}

	kubeconfigPath := firstKubeconfigPath()
	if kubeconfigPath == "" {
		t.Skip("no kubeconfig available for the Kubernetes Alloy delivery test")
	}
	restConfig, err := clientcmd.BuildConfigFromFlags("", kubeconfigPath)
	if err != nil {
		t.Fatalf("load kubeconfig %s: %v", kubeconfigPath, err)
	}
	clientset, err := kubernetes.NewForConfig(restConfig)
	if err != nil {
		t.Fatalf("create Kubernetes client: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	namespace, err := clientset.CoreV1().Namespaces().Create(ctx, &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{GenerateName: "alloy-k8s-e2e-"},
	}, metav1.CreateOptions{})
	if err != nil {
		t.Fatalf("create namespace: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cleanupCancel()
		_ = clientset.CoreV1().Namespaces().Delete(cleanupCtx, namespace.Name, metav1.DeleteOptions{})
	})

	line := `{"ts":"2026-11-02T10:00:00Z","level":"info","msg":"alloy k8s e2e","component":"controller","exporter":"board-1","lease_id":"lease-k8s-e2e","client":"client-k8s-e2e","build_id":"build-k8s-e2e"}`
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: "alloy-log-source-",
			Namespace:    namespace.Name,
			Labels: map[string]string{
				"jumpstarter.dev/log-scrape": "true",
				"jumpstarter.dev/log-format": "json",
			},
		},
		Spec: corev1.PodSpec{
			RestartPolicy: corev1.RestartPolicyNever,
			Containers: []corev1.Container{{
				Name:    "log-source",
				Image:   "curlimages/curl:8.10.1",
				Command: []string{"/bin/sh", "-c"},
				Args:    []string{fmt.Sprintf("while true; do printf '%%s\\n' '%s'; sleep 1; done", line)},
			}},
		},
	}
	createdPod, err := clientset.CoreV1().Pods(namespace.Name).Create(ctx, pod, metav1.CreateOptions{})
	if err != nil {
		t.Fatalf("create log source pod: %v", err)
	}
	waitForPodRunning(t, ctx, clientset, namespace.Name, createdPod.Name)

	receiverPort := freeLoopbackPort(t)
	serverPort := freeLoopbackPort(t)
	workDir := t.TempDir()
	configPath := filepath.Join(workDir, "config.alloy")
	storagePath := filepath.Join(workDir, "data")
	receiverURL := fmt.Sprintf("http://127.0.0.1:%d/loki/api/v1/push", receiverPort)
	readyURL := fmt.Sprintf("http://127.0.0.1:%d/ready", receiverPort)

	config := fmt.Sprintf(`loki.source.api "test" {
	http {
		listen_address = "127.0.0.1"
		listen_port    = %d
	}
	forward_to = [loki.echo.test.receiver]
}

loki.echo "test" {}

discovery.kubernetes "jumpstarter_pods" {
	role             = "pod"
	kubeconfig_file  = %q

	namespaces {
		names = [%q]
	}

	selectors {
		role  = "pod"
		label = "jumpstarter.dev/log-scrape=true"
	}
}

discovery.relabel "jumpstarter_pods" {
	targets = discovery.kubernetes.jumpstarter_pods.targets

	rule {
		source_labels = ["__meta_kubernetes_pod_label_jumpstarter_dev_log_scrape"]
		regex         = "true"
		action        = "keep"
	}

	rule {
		source_labels = ["__meta_kubernetes_namespace"]
		target_label  = "namespace"
	}
}

loki.source.kubernetes "jumpstarter" {
	targets    = discovery.relabel.jumpstarter_pods.output
	forward_to = [loki.process.jumpstarter.receiver]

	client {
		kubeconfig_file = %q
	}
}

%s`, receiverPort, kubeconfigPath, namespace.Name, kubeconfigPath, fmt.Sprintf(alloyProcessWriteTemplate, receiverURL))

	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write Alloy config: %v", err)
	}

	cmd := exec.Command( //nolint:gosec // test-controlled binary and paths
		alloyBin,
		"run",
		"--disable-reporting",
		fmt.Sprintf("--server.http.listen-addr=127.0.0.1:%d", serverPort),
		"--storage.path="+storagePath,
		configPath,
	)
	logs := &alloyLogBuffer{}
	cmd.Stdout = logs
	cmd.Stderr = logs
	if err := cmd.Start(); err != nil {
		t.Fatalf("start Alloy: %v", err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Signal(syscall.SIGTERM)
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
	})

	waitForHTTP(t, readyURL, 15*time.Second, logs)
	received := waitForLogLine(t, logs, "lease-k8s-e2e", 60*time.Second)
	labels, entry := parseEchoedEntry(t, received)
	if labels["component"] != "controller" || labels["exporter"] != "board-1" || labels["namespace"] != namespace.Name {
		t.Fatalf("unexpected Kubernetes stream labels: %v", labels)
	}
	for _, forbidden := range []string{"job", "pod", "container", "filename"} {
		if _, ok := labels[forbidden]; ok {
			t.Fatalf("stream labels contain forbidden label %q: %v", forbidden, labels)
		}
	}

	var body map[string]any
	if err := json.Unmarshal([]byte(entry), &body); err != nil {
		t.Fatalf("parse delivered JSON body: %v\nentry: %s", err, entry)
	}
	for key, want := range map[string]string{
		"lease_id": "lease-k8s-e2e",
		"client":   "client-k8s-e2e",
		"build_id": "build-k8s-e2e",
	} {
		if got, ok := body[key].(string); !ok || got != want {
			t.Fatalf("body[%q] = %v, want %q; body: %v", key, body[key], want, body)
		}
	}
}

func firstKubeconfigPath() string {
	path := os.Getenv("KUBECONFIG")
	if path == "" {
		path = clientcmd.RecommendedHomeFile
	}
	if path == "" {
		return ""
	}
	// KUBECONFIG can contain a path list; Alloy accepts one file.
	path = strings.Split(path, string(os.PathListSeparator))[0]
	if _, err := os.Stat(path); err != nil {
		return ""
	}
	return path
}

func waitForPodRunning(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, name string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Minute)
	for time.Now().Before(deadline) {
		pod, err := clientset.CoreV1().Pods(namespace).Get(ctx, name, metav1.GetOptions{})
		if err == nil && pod.Status.Phase == corev1.PodRunning {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for pod %s/%s to run", namespace, name)
}
