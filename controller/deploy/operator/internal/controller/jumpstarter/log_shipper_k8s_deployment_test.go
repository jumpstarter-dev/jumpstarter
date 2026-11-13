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
	"strconv"
	"testing"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

const (
	shipperKubernetesE2E  = "SHIPPER_K8S_E2E"
	defaultLokiImage      = "grafana/loki:3.7.9"
	defaultAlloyImage     = "grafana/alloy:v1.21.3"
	defaultVectorImage    = "timberio/vector:0.61.0-debian"
	defaultPromtailImage  = "grafana/promtail:3.6.0"
	defaultLogImage       = "busybox:1.36"
	shipperConfigName     = "jumpstarter-shipper-config"
	shipperServiceAccount = "jumpstarter-shipper"
)

// TestKubernetesShipperDeployment deploys the generated Alloy and Vector
// ConfigMaps as real Kubernetes DaemonSets and verifies delivery through a
// real in-cluster Loki. It is gated because it needs a live cluster and can
// pull the pinned shipper/Loki images.
func TestKubernetesShipperDeployment(t *testing.T) {
	if os.Getenv(shipperKubernetesE2E) != "1" {
		t.Skip("set SHIPPER_K8S_E2E=1 to run the Kubernetes shipper deployment test")
	}

	kubeconfigPath := firstKubeconfigPath()
	if kubeconfigPath == "" {
		t.Skip("no kubeconfig available for the Kubernetes shipper deployment test")
	}
	restConfig, err := clientcmd.BuildConfigFromFlags("", kubeconfigPath)
	if err != nil {
		t.Fatalf("load kubeconfig %s: %v", kubeconfigPath, err)
	}
	clientset, err := kubernetes.NewForConfig(restConfig)
	if err != nil {
		t.Fatalf("create Kubernetes client: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 12*time.Minute)
	defer cancel()

	namespace, err := clientset.CoreV1().Namespaces().Create(ctx, &corev1.Namespace{
		ObjectMeta: metav1.ObjectMeta{GenerateName: "shipper-k8s-e2e-"},
	}, metav1.CreateOptions{})
	if err != nil {
		t.Fatalf("create test namespace: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cleanupCancel()
		_ = clientset.CoreV1().Namespaces().Delete(cleanupCtx, namespace.Name, metav1.DeleteOptions{})
		for {
			_, err := clientset.CoreV1().Namespaces().Get(cleanupCtx, namespace.Name, metav1.GetOptions{})
			if apierrors.IsNotFound(err) {
				return
			}
			select {
			case <-cleanupCtx.Done():
				return
			case <-time.After(200 * time.Millisecond):
			}
		}
	})

	createLokiForKubernetesTest(t, ctx, clientset, namespace.Name)
	createShipperRBAC(t, ctx, clientset, namespace.Name)

	for _, shipper := range []string{shipperTypePromtail, shipperTypeAlloy, shipperTypeVector} {
		t.Run(shipper, func(t *testing.T) {
			runKubernetesShipperCase(t, ctx, clientset, namespace.Name, shipper)
		})
	}
}

func runKubernetesShipperCase(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, shipper string) {
	t.Helper()

	run := fmt.Sprintf("%s-k8s-%d", shipper, time.Now().UnixNano())
	withMarker := run + "-with"
	withoutMarker := run + "-without"
	withExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:00Z","level":"info","msg":"k8s with exporter","component":"controller","exporter":"board-k8s","lease_id":%q,"client":"client-k8s","build_id":"build-k8s","error":"line1\nline2"}`, withMarker)
	withoutExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:01Z","level":"info","msg":"k8s without exporter","component":"controller","lease_id":%q,"client":"client-k8s","build_id":"build-k8s"}`, withoutMarker)

	logPod := createLogSourcePod(t, ctx, clientset, namespace, withExporter, withoutExporter)
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cleanupCancel()
		_ = clientset.CoreV1().Pods(namespace).Delete(cleanupCtx, logPod.Name, metav1.DeleteOptions{})
	})

	js := testJumpstarter()
	js.Namespace = namespace
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{
		Loki: operatorv1alpha1.TelemetryLokiConfig{URL: "http://loki:3100/loki/api/v1/push"},
	}
	configKey, config := renderShipper(js, shipper)

	configMap := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Name: shipperConfigName, Namespace: namespace},
		Data:       map[string]string{configKey: config},
	}
	if _, err := clientset.CoreV1().ConfigMaps(namespace).Create(ctx, configMap, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create shipper ConfigMap: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cleanupCancel()
		_ = clientset.CoreV1().ConfigMaps(namespace).Delete(cleanupCtx, configMap.Name, metav1.DeleteOptions{})
	})

	daemonSet := buildShipperDaemonSet(namespace, shipper, configKey)
	if _, err := clientset.AppsV1().DaemonSets(namespace).Create(ctx, daemonSet, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create %s DaemonSet: %v", shipper, err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cleanupCancel()
		_ = clientset.AppsV1().DaemonSets(namespace).Delete(cleanupCtx, daemonSet.Name, metav1.DeleteOptions{})
	})

	waitForDaemonSetPod(t, ctx, clientset, namespace, daemonSet.Spec.Template.Labels["app"])
	entries := waitForLokiServiceEntries(t, ctx, clientset, namespace, run, 2, 2*time.Minute)

	withEntry := findLokiEntry(t, entries, withMarker)
	requireLokiLabels(t, withEntry, map[string]string{
		"component": "controller",
		"exporter":  "board-k8s",
		"namespace": namespace,
	})
	withoutEntry := findLokiEntry(t, entries, withoutMarker)
	requireLokiLabels(t, withoutEntry, map[string]string{
		"component": "controller",
		"namespace": namespace,
	})

	withBody := parseLokiJSONBody(t, withEntry)
	if got := withBody["lease_id"]; got != withMarker {
		t.Fatalf("with-exporter lease_id = %v, want %s", got, withMarker)
	}
	if got := withBody["error"]; got != "line1\nline2" {
		t.Fatalf("with-exporter error = %v, want multi-line value", got)
	}
	withoutBody := parseLokiJSONBody(t, withoutEntry)
	if got := withoutBody["lease_id"]; got != withoutMarker {
		t.Fatalf("without-exporter lease_id = %v, want %s", got, withoutMarker)
	}
}

func createLokiForKubernetesTest(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace string) {
	t.Helper()

	config := `auth_enabled: false

server:
  http_listen_address: 0.0.0.0
  http_listen_port: 3100
  grpc_listen_address: 0.0.0.0
  grpc_listen_port: 9096

common:
  path_prefix: /tmp/loki
  storage:
    filesystem:
      chunks_directory: /tmp/loki/chunks
      rules_directory: /tmp/loki/rules
  replication_factor: 1
  ring:
    instance_addr: 127.0.0.1
    instance_port: 9096
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-04-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

storage_config:
  tsdb_shipper:
    active_index_directory: /tmp/loki/tsdb-index
    cache_location: /tmp/loki/tsdb-cache

limits_config:
  allow_structured_metadata: true
  discover_service_name: []
  discover_log_levels: false
`
	configMap := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Name: "loki-config", Namespace: namespace},
		Data:       map[string]string{"loki.yaml": config},
	}
	if _, err := clientset.CoreV1().ConfigMaps(namespace).Create(ctx, configMap, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create Loki ConfigMap: %v", err)
	}

	labels := map[string]string{"app": "loki"}
	replicas := int32(1)
	deployment := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: "loki", Namespace: namespace},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					Tolerations: controlPlaneTolerations(),
					Containers: []corev1.Container{{
						Name:  "loki",
						Image: envOrDefault("LOKI_IMAGE", defaultLokiImage),
						Args:  []string{"-config.file=/etc/loki/loki.yaml"},
						Ports: []corev1.ContainerPort{{Name: "http", ContainerPort: 3100}},
						ReadinessProbe: &corev1.Probe{
							ProbeHandler: corev1.ProbeHandler{
								HTTPGet: &corev1.HTTPGetAction{Path: "/ready", Port: intstr.FromInt32(3100)},
							},
							InitialDelaySeconds: 5,
							PeriodSeconds:       5,
						},
						VolumeMounts: []corev1.VolumeMount{{
							Name:      "config",
							MountPath: "/etc/loki",
							ReadOnly:  true,
						}, {
							Name:      "data",
							MountPath: "/tmp/loki",
						}},
					}},
					Volumes: []corev1.Volume{{
						Name: "config",
						VolumeSource: corev1.VolumeSource{
							ConfigMap: &corev1.ConfigMapVolumeSource{
								LocalObjectReference: corev1.LocalObjectReference{Name: configMap.Name},
							},
						},
					}, {
						Name:         "data",
						VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
					}},
				},
			},
		},
	}
	if _, err := clientset.AppsV1().Deployments(namespace).Create(ctx, deployment, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create Loki Deployment: %v", err)
	}

	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: "loki", Namespace: namespace},
		Spec: corev1.ServiceSpec{
			Selector: labels,
			Ports: []corev1.ServicePort{{
				Name:       "http",
				Port:       3100,
				TargetPort: intstr.FromInt32(3100),
			}},
		},
	}
	if _, err := clientset.CoreV1().Services(namespace).Create(ctx, service, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create Loki Service: %v", err)
	}

	pod := waitForDeploymentPod(t, ctx, clientset, namespace, "app=loki")
	waitForPodReady(t, ctx, clientset, namespace, pod.Name, 2*time.Minute)
}

func createShipperRBAC(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace string) {
	t.Helper()

	serviceAccount := &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{Name: shipperServiceAccount, Namespace: namespace},
	}
	if _, err := clientset.CoreV1().ServiceAccounts(namespace).Create(ctx, serviceAccount, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create shipper ServiceAccount: %v", err)
	}

	roleName := "jumpstarter-shipper-e2e-" + namespace
	role := &rbacv1.ClusterRole{
		ObjectMeta: metav1.ObjectMeta{Name: roleName},
		Rules: []rbacv1.PolicyRule{{
			APIGroups: []string{""},
			Resources: []string{"pods", "pods/log", "namespaces", "nodes"},
			Verbs:     []string{"get", "list", "watch"},
		}},
	}
	if _, err := clientset.RbacV1().ClusterRoles().Create(ctx, role, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create shipper ClusterRole: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cleanupCancel()
		_ = clientset.RbacV1().ClusterRoles().Delete(cleanupCtx, roleName, metav1.DeleteOptions{})
	})

	binding := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: roleName},
		RoleRef: rbacv1.RoleRef{
			APIGroup: rbacv1.GroupName,
			Kind:     "ClusterRole",
			Name:     roleName,
		},
		Subjects: []rbacv1.Subject{{
			Kind:      rbacv1.ServiceAccountKind,
			Name:      shipperServiceAccount,
			Namespace: namespace,
		}},
	}
	if _, err := clientset.RbacV1().ClusterRoleBindings().Create(ctx, binding, metav1.CreateOptions{}); err != nil {
		t.Fatalf("create shipper ClusterRoleBinding: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cleanupCancel()
		_ = clientset.RbacV1().ClusterRoleBindings().Delete(cleanupCtx, roleName, metav1.DeleteOptions{})
	})
}

func createLogSourcePod(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, withExporter, withoutExporter string) *corev1.Pod {
	t.Helper()
	script := fmt.Sprintf("while true; do printf '%%s\\n' '%s' '%s'; sleep 1; done", withExporter, withoutExporter)
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: "jumpstarter-log-source-",
			Namespace:    namespace,
			Labels: map[string]string{
				"jumpstarter.dev/log-scrape": "true",
				"component":                  "controller",
			},
		},
		Spec: corev1.PodSpec{
			RestartPolicy: corev1.RestartPolicyNever,
			Tolerations:   controlPlaneTolerations(),
			Containers: []corev1.Container{{
				Name:            "log-source",
				Image:           envOrDefault("LOG_SOURCE_IMAGE", defaultLogImage),
				ImagePullPolicy: corev1.PullIfNotPresent,
				Command:         []string{"/bin/sh", "-c"},
				Args:            []string{script},
			}},
		},
	}
	created, err := clientset.CoreV1().Pods(namespace).Create(ctx, pod, metav1.CreateOptions{})
	if err != nil {
		t.Fatalf("create log source Pod: %v", err)
	}
	waitForPodRunning(t, ctx, clientset, namespace, created.Name)
	return created
}

func buildShipperDaemonSet(namespace, shipper, configKey string) *appsv1.DaemonSet {
	name := shipper + "-shipper"
	labels := map[string]string{"app": name}
	configFile := "config.alloy"
	switch shipper {
	case shipperTypeVector:
		configFile = "vector.yaml"
	case shipperTypePromtail:
		configFile = "promtail.yaml"
	}

	container := corev1.Container{
		Name:            shipper,
		Image:           shipperImage(shipper),
		ImagePullPolicy: corev1.PullIfNotPresent,
		VolumeMounts: []corev1.VolumeMount{{
			Name:      "config",
			MountPath: "/etc/" + shipper,
			ReadOnly:  true,
		}, {
			Name:      "data",
			MountPath: "/var/lib/" + shipper,
		}},
	}
	volumes := []corev1.Volume{{
		Name: "config",
		VolumeSource: corev1.VolumeSource{
			ConfigMap: &corev1.ConfigMapVolumeSource{
				LocalObjectReference: corev1.LocalObjectReference{Name: shipperConfigName},
				Items:                []corev1.KeyToPath{{Key: configKey, Path: configFile}},
			},
		},
	}, {
		Name:         "data",
		VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
	}}

	hostPathType := corev1.HostPathDirectory
	switch shipper {
	case shipperTypeAlloy:
		container.Args = []string{
			"run",
			"--disable-reporting",
			"--server.http.listen-addr=0.0.0.0:12345",
			"--storage.path=/var/lib/alloy/data",
			"/etc/alloy/config.alloy",
		}
		container.Env = []corev1.EnvVar{nodeNameEnv("HOSTNAME")}
	case shipperTypeVector:
		container.Args = []string{"--config", "/etc/vector/vector.yaml"}
		container.Env = []corev1.EnvVar{nodeNameEnv("VECTOR_SELF_NODE_NAME")}
		volumes = append(volumes, corev1.Volume{
			Name: "pod-logs",
			VolumeSource: corev1.VolumeSource{
				HostPath: &corev1.HostPathVolumeSource{Path: "/var/log/pods", Type: &hostPathType},
			},
		})
		container.VolumeMounts = append(container.VolumeMounts, corev1.VolumeMount{
			Name:      "pod-logs",
			MountPath: "/var/log/pods",
			ReadOnly:  true,
		})
	case shipperTypePromtail:
		container.Args = []string{
			"-config.file=/etc/promtail/promtail.yaml",
			"-config.expand-env",
		}
		container.Env = []corev1.EnvVar{nodeNameEnv("HOSTNAME")}
		volumes = append(volumes, corev1.Volume{
			Name: "pod-logs",
			VolumeSource: corev1.VolumeSource{
				HostPath: &corev1.HostPathVolumeSource{Path: "/var/log/pods", Type: &hostPathType},
			},
		}, corev1.Volume{
			Name:         "positions",
			VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
		})
		container.VolumeMounts = append(container.VolumeMounts,
			corev1.VolumeMount{Name: "pod-logs", MountPath: "/var/log/pods", ReadOnly: true},
			corev1.VolumeMount{Name: "positions", MountPath: "/run/promtail"},
		)
	}

	return &appsv1.DaemonSet{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: namespace},
		Spec: appsv1.DaemonSetSpec{
			Selector: &metav1.LabelSelector{MatchLabels: labels},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: labels},
				Spec: corev1.PodSpec{
					ServiceAccountName: shipperServiceAccount,
					Tolerations:        controlPlaneTolerations(),
					Containers:         []corev1.Container{container},
					Volumes:            volumes,
				},
			},
		},
	}
}

func shipperImage(shipper string) string {
	switch shipper {
	case shipperTypeAlloy:
		return envOrDefault("ALLOY_IMAGE", defaultAlloyImage)
	case shipperTypeVector:
		return envOrDefault("VECTOR_IMAGE", defaultVectorImage)
	case shipperTypePromtail:
		return envOrDefault("PROMTAIL_IMAGE", defaultPromtailImage)
	default:
		return ""
	}
}

func envOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func nodeNameEnv(name string) corev1.EnvVar {
	return corev1.EnvVar{
		Name: name,
		ValueFrom: &corev1.EnvVarSource{
			FieldRef: &corev1.ObjectFieldSelector{FieldPath: "spec.nodeName"},
		},
	}
}

func controlPlaneTolerations() []corev1.Toleration {
	return []corev1.Toleration{
		{Key: "node-role.kubernetes.io/control-plane", Operator: corev1.TolerationOpExists, Effect: corev1.TaintEffectNoSchedule},
		{Key: "node-role.kubernetes.io/master", Operator: corev1.TolerationOpExists, Effect: corev1.TaintEffectNoSchedule},
	}
}

func waitForDeploymentPod(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, selector string) *corev1.Pod {
	t.Helper()
	deadline := time.Now().Add(3 * time.Minute)
	for time.Now().Before(deadline) {
		pods, err := clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{LabelSelector: selector})
		if err == nil {
			for i := range pods.Items {
				pod := &pods.Items[i]
				if pod.DeletionTimestamp.IsZero() {
					return pod
				}
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for deployment pod with selector %q", selector)
	return nil
}

func waitForPodReady(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, name string, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		pod, err := clientset.CoreV1().Pods(namespace).Get(ctx, name, metav1.GetOptions{})
		if err == nil && pod.Status.Phase == corev1.PodRunning {
			for _, condition := range pod.Status.Conditions {
				if condition.Type == corev1.PodReady && condition.Status == corev1.ConditionTrue {
					return
				}
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	pod, _ := clientset.CoreV1().Pods(namespace).Get(ctx, name, metav1.GetOptions{})
	t.Fatalf("timed out waiting for pod %s/%s to be ready: %+v", namespace, name, pod)
}

func waitForDaemonSetPod(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, app string) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Minute)
	for time.Now().Before(deadline) {
		pods, err := clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{LabelSelector: "app=" + app})
		if err == nil {
			for i := range pods.Items {
				pod := &pods.Items[i]
				if pod.DeletionTimestamp.IsZero() && pod.Status.Phase == corev1.PodRunning {
					return
				}
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for DaemonSet pod %s/%s", namespace, app)
}

func waitForLokiServiceEntries(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace, marker string, want int, timeout time.Duration) []lokiEntry {
	t.Helper()
	query := fmt.Sprintf(`{component="controller",namespace=%q} |= %q`, namespace, marker)
	start := time.Now().Add(-time.Minute).UnixNano()
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		end := time.Now().Add(time.Minute).UnixNano()
		req := clientset.CoreV1().Services(namespace).ProxyGet(
			"http", "loki", "3100", "/loki/api/v1/query_range",
			map[string]string{
				"query":     query,
				"start":     strconv.FormatInt(start, 10),
				"end":       strconv.FormatInt(end, 10),
				"limit":     "100",
				"direction": "backward",
			},
		)
		raw, err := req.DoRaw(ctx)
		if err != nil {
			lastErr = err
		} else {
			var result lokiQueryResponse
			if err := json.Unmarshal(raw, &result); err != nil {
				lastErr = fmt.Errorf("decode Loki response: %w", err)
			} else {
				entries, err := lokiEntriesFromResponse(result)
				if err != nil {
					lastErr = err
				} else if len(entries) >= want {
					return entries
				}
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	dumpShipperDiagnostics(t, ctx, clientset, namespace)
	t.Fatalf("timed out waiting for %d Loki entries containing %q via service proxy: %v", want, marker, lastErr)
	return nil
}

func dumpShipperDiagnostics(t *testing.T, ctx context.Context, clientset *kubernetes.Clientset, namespace string) {
	t.Helper()
	pods, err := clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		t.Logf("list pods for diagnostics: %v", err)
		return
	}
	for i := range pods.Items {
		pod := &pods.Items[i]
		t.Logf("diagnostic pod %s phase=%s labels=%v", pod.Name, pod.Status.Phase, pod.Labels)
		for _, container := range pod.Spec.Containers {
			raw, err := clientset.CoreV1().Pods(namespace).GetLogs(pod.Name, &corev1.PodLogOptions{
				Container: container.Name,
				TailLines: new(int64(100)),
			}).DoRaw(ctx)
			if err != nil {
				t.Logf("logs %s/%s: %v", pod.Name, container.Name, err)
				continue
			}
			t.Logf("logs %s/%s:\n%s", pod.Name, container.Name, raw)
		}
	}
	cm, err := clientset.CoreV1().ConfigMaps(namespace).Get(ctx, shipperConfigName, metav1.GetOptions{})
	if err == nil {
		for key, value := range cm.Data {
			t.Logf("diagnostic config %s:\n%s", key, value)
		}
	}
}
