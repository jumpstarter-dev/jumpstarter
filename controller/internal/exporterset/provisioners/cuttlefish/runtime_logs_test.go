package cuttlefish

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRuntimeStreamsLauncherLogs(t *testing.T) {
	for _, backend := range []string{backendHTTP, backendExec} {
		t.Run(backend, func(t *testing.T) {
			pod := renderTestPod(t, map[string]any{"fetch_images": true, "backend": backend})
			script := initContainer(t, pod, runtimeContainerName).Command[2]
			if !strings.Contains(script, launcherLogsCommand(cvdStatePath)) {
				t.Fatal("runtime must stream launcher logs for both backends")
			}
		})
	}
}

func TestLauncherLogFollower(t *testing.T) {
	tail, err := exec.LookPath("tail")
	if err != nil {
		t.Skip("tail is unavailable")
	}
	if output, err := exec.Command(tail, "--version").Output(); err != nil || !strings.Contains(string(output), "GNU coreutils") {
		tail, err = exec.LookPath("gtail")
		if err != nil {
			t.Skip("runtime log follower requires GNU tail")
		}
	}
	root := filepath.Join(t.TempDir(), "cvd state")
	if err := os.Mkdir(root, 0o755); err != nil {
		t.Fatal(err)
	}
	output, err := os.CreateTemp(t.TempDir(), "runtime-output")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := output.Close(); err != nil {
			t.Errorf("close runtime output: %v", err)
		}
	})

	definition, _, ok := strings.Cut(launcherLogsCommand(root), "\nstream_launcher_logs ")
	if !ok {
		t.Fatal("missing follower entry point")
	}
	// Kubelet resolves escaped dollar signs before passing commands to Bash.
	definition = strings.ReplaceAll(definition, "$$", "$")
	// Exercise the emitted shell function with a short polling interval and
	// the host's GNU tail, while keeping the runtime command unchanged.
	script := "tail() { exec \"$TEST_TAIL\" \"$@\"; }\n" + definition + "\nstream_launcher_logs \"$TEST_STATE\" 0.01\n"
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	t.Cleanup(cancel)
	cmd := exec.CommandContext(ctx, "bash", "-ec", script)
	cmd.Env = append(os.Environ(), "TEST_TAIL="+tail, "TEST_STATE="+root)
	cmd.Stdout, cmd.Stderr = output, output
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Signal(os.Interrupt)
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
	})

	waitFor := func(text string) {
		t.Helper()
		deadline := time.Now().Add(5 * time.Second)
		for time.Now().Before(deadline) {
			data, err := os.ReadFile(output.Name())
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(data), text) {
				return
			}
			time.Sleep(10 * time.Millisecond)
		}
		data, _ := os.ReadFile(output.Name())
		t.Fatalf("launcher output did not contain %q: %s", text, data)
	}
	writeLog := func(path, text string, appendLog bool) {
		t.Helper()
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		flags := os.O_CREATE | os.O_WRONLY | os.O_TRUNC
		if appendLog {
			flags = os.O_CREATE | os.O_WRONLY | os.O_APPEND
		}
		file, err := os.OpenFile(path, flags, 0o644)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.WriteString(text); err != nil {
			t.Fatal(err)
		}
		if err := file.Close(); err != nil {
			t.Fatal(err)
		}
	}
	log := filepath.Join(root, "group one", "cuttlefish", "instances", "cvd-1", "logs", "launcher.log")
	writeLog(log, "initial boot\n", false)
	waitFor("initial boot\n")
	writeLog(log, "guest is running\n", true)
	waitFor("guest is running\n")

	if err := os.Rename(log, log+".old"); err != nil {
		t.Fatal(err)
	}
	writeLog(log, "rotated launcher\n", false)
	waitFor("rotated launcher\n")
	writeLog(log, "after rotation\n", true)
	waitFor("after rotation\n")

	if err := os.RemoveAll(filepath.Join(root, "group one")); err != nil {
		t.Fatal(err)
	}
	log = filepath.Join(root, "group two", "cuttlefish", "instances", "cvd-1", "logs", "launcher.log")
	writeLog(log, "recreated CVD\n", false)
	waitFor("recreated CVD\n")
	writeLog(log, "second boot\n", true)
	waitFor("second boot\n")

	data, err := os.ReadFile(output.Name())
	if err != nil {
		t.Fatal(err)
	}
	for _, line := range []string{"initial boot", "guest is running", "rotated launcher", "after rotation", "recreated CVD", "second boot"} {
		if strings.Count(string(data), line+"\n") != 1 {
			t.Fatalf("duplicated launcher line %q: %s", line, data)
		}
	}
}
