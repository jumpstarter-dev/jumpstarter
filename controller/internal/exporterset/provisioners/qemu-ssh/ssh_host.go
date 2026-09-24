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

// Package qemussh implements the qemu-ssh.jumpstarter.dev provisioner
// for ExporterSets. It deploys exporter + QEMU runtime containers on
// remote lab hosts via SSH, using Podman quadlets for container
// orchestration.
package qemussh

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"regexp"
	"sync"
	"time"

	"github.com/google/go-cmp/cmp"
	"github.com/pkg/sftp"
	"golang.org/x/crypto/ssh"
)

// RemoteHost abstracts remote host management. SSH is the first
// implementation; macOS launchd or agent-based hosts can follow.
type RemoteHost interface {
	// RunCommand executes a command and returns the result.
	RunCommand(ctx context.Context, cmd string) (CommandResult, error)

	// ReconcileFile writes content to path only if it differs from
	// the existing file. Returns whether the file was changed and a
	// sanitized diff string for logging.
	ReconcileFile(ctx context.Context, path, content string) (changed bool, diff string, err error)

	// RemoveFile deletes a file if it exists. No error if absent.
	RemoveFile(ctx context.Context, path string) error

	// MkdirAll creates a directory and all parents.
	MkdirAll(ctx context.Context, path string) error

	// Close releases the connection.
	Close() error
}

// CommandResult holds the output from a remote command execution.
type CommandResult struct {
	Stdout   string
	Stderr   string
	ExitCode int
}

// SSHHost implements RemoteHost via SSH/SFTP.
type SSHHost struct {
	sshClient  *ssh.Client
	sftpClient *sftp.Client
	hostName   string
	mu         sync.Mutex
}

// SSHConnectConfig holds the parameters needed to establish an SSH connection.
type SSHConnectConfig struct {
	Host       string
	Port       int
	User       string
	PrivateKey []byte
}

// Connect establishes an SSH + SFTP connection to a remote host.
func Connect(cfg SSHConnectConfig) (*SSHHost, error) {
	if cfg.Port == 0 {
		cfg.Port = 22
	}

	signer, err := ssh.ParsePrivateKey(cfg.PrivateKey)
	if err != nil {
		return nil, fmt.Errorf("parse SSH private key: %w", err)
	}

	sshConfig := &ssh.ClientConfig{
		User: cfg.User,
		Auth: []ssh.AuthMethod{
			ssh.PublicKeys(signer),
		},
		HostKeyCallback: ssh.InsecureIgnoreHostKey(), //nolint:gosec // lab hosts; TODO: make configurable
		Timeout:         30 * time.Second,
	}

	addr := fmt.Sprintf("%s:%d", cfg.Host, cfg.Port)

	sshClient, err := ssh.Dial("tcp", addr, sshConfig)
	if err != nil {
		return nil, fmt.Errorf("SSH dial %s: %w", addr, err)
	}

	sftpClient, err := sftp.NewClient(sshClient)
	if err != nil {
		_ = sshClient.Close()
		return nil, fmt.Errorf("SFTP client for %s: %w", addr, err)
	}

	return &SSHHost{
		sshClient:  sshClient,
		sftpClient: sftpClient,
		hostName:   cfg.Host,
	}, nil
}

// defaultCommandTimeout is the maximum duration for a single SSH command.
const defaultCommandTimeout = 2 * time.Minute

// RunCommand executes a command on the remote host. The context is
// used for cancellation: if it expires, the SSH session is closed
// and the command is interrupted.
func (h *SSHHost) RunCommand(ctx context.Context, command string) (CommandResult, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	session, err := h.sshClient.NewSession()
	if err != nil {
		return CommandResult{}, fmt.Errorf("SSH session on %s: %w", h.hostName, err)
	}
	defer session.Close() //nolint:errcheck

	// If no deadline is set on the context, apply a default timeout.
	if _, hasDeadline := ctx.Deadline(); !hasDeadline {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, defaultCommandTimeout)
		defer cancel()
	}

	// Close the session when the context expires to unblock Run.
	// The done channel ensures the goroutine exits when the command
	// completes, even if the context has a long or no deadline.
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			_ = session.Close()
		case <-done:
		}
	}()

	stdout, err := session.StdoutPipe()
	if err != nil {
		return CommandResult{}, fmt.Errorf("stdout pipe on %s: %w", h.hostName, err)
	}

	stderr, err := session.StderrPipe()
	if err != nil {
		return CommandResult{}, fmt.Errorf("stderr pipe on %s: %w", h.hostName, err)
	}

	var stdoutBytes, stderrBytes []byte
	var stdoutErr, stderrErr error
	var wg sync.WaitGroup

	wg.Add(2)
	go func() {
		defer wg.Done()
		stdoutBytes, stdoutErr = io.ReadAll(stdout)
	}()
	go func() {
		defer wg.Done()
		stderrBytes, stderrErr = io.ReadAll(stderr)
	}()

	runErr := session.Run(command)
	wg.Wait()

	if ctx.Err() != nil {
		return CommandResult{}, fmt.Errorf("command on %s timed out: %w", h.hostName, ctx.Err())
	}

	if stdoutErr != nil {
		return CommandResult{}, fmt.Errorf("read stdout on %s: %w", h.hostName, stdoutErr)
	}
	if stderrErr != nil {
		return CommandResult{}, fmt.Errorf("read stderr on %s: %w", h.hostName, stderrErr)
	}

	exitCode := 0
	if runErr != nil {
		if exitErr, ok := runErr.(*ssh.ExitError); ok {
			exitCode = exitErr.ExitStatus()
		} else {
			return CommandResult{}, fmt.Errorf("run command on %s: %w", h.hostName, runErr)
		}
	}

	return CommandResult{
		Stdout:   string(stdoutBytes),
		Stderr:   string(stderrBytes),
		ExitCode: exitCode,
	}, nil
}

// ReconcileFile writes content to path only if the file doesn't exist
// or its content differs. Returns whether the file changed and a
// sanitized diff for logging.
func (h *SSHHost) ReconcileFile(ctx context.Context, path, content string) (bool, string, error) {
	if err := ctx.Err(); err != nil {
		return false, "", fmt.Errorf("reconcile %s on %s: %w", path, h.hostName, err)
	}

	h.mu.Lock()
	defer h.mu.Unlock()

	existing, err := h.readFile(path)
	if err != nil {
		if !isNotExist(err) {
			return false, "", fmt.Errorf("read %s on %s: %w", path, h.hostName, err)
		}
		// File doesn't exist — create it.
		if err := h.writeFile(path, content); err != nil {
			return false, "", fmt.Errorf("create %s on %s: %w", path, h.hostName, err)
		}
		return true, fmt.Sprintf("created %s", path), nil
	}

	if existing == content {
		return false, "", nil
	}

	diff := SanitizeDiff(cmp.Diff(existing, content))

	if err := h.writeFile(path, content); err != nil {
		return false, "", fmt.Errorf("update %s on %s: %w", path, h.hostName, err)
	}

	return true, diff, nil
}

// RemoveFile deletes a file. No error if the file doesn't exist.
func (h *SSHHost) RemoveFile(ctx context.Context, path string) error {
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("remove %s on %s: %w", path, h.hostName, err)
	}

	h.mu.Lock()
	defer h.mu.Unlock()

	err := h.sftpClient.Remove(path)
	if err != nil && !isNotExist(err) {
		return fmt.Errorf("remove %s on %s: %w", path, h.hostName, err)
	}
	return nil
}

// MkdirAll creates a directory and all parents on the remote host.
func (h *SSHHost) MkdirAll(ctx context.Context, path string) error {
	if err := ctx.Err(); err != nil {
		return fmt.Errorf("mkdir %s on %s: %w", path, h.hostName, err)
	}

	h.mu.Lock()
	defer h.mu.Unlock()

	return h.sftpClient.MkdirAll(path)
}

// Close releases the SSH and SFTP connections.
func (h *SSHHost) Close() error {
	var sftpErr, sshErr error
	if h.sftpClient != nil {
		sftpErr = h.sftpClient.Close()
	}
	if h.sshClient != nil {
		sshErr = h.sshClient.Close()
	}
	if sshErr != nil {
		return sshErr
	}
	return sftpErr
}

// readFile reads a remote file via SFTP. Returns an error if it
// doesn't exist.
func (h *SSHHost) readFile(path string) (string, error) {
	f, err := h.sftpClient.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close() //nolint:errcheck

	data, err := io.ReadAll(f)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

// writeFile creates or overwrites a remote file, creating parent
// directories if necessary.
func (h *SSHHost) writeFile(path, content string) error {
	parentDir := filepath.Dir(path)
	if parentDir != "/" && parentDir != "." {
		if err := h.sftpClient.MkdirAll(parentDir); err != nil {
			return fmt.Errorf("mkdir %s: %w", parentDir, err)
		}
	}

	f, err := h.sftpClient.Create(path)
	if err != nil {
		return err
	}

	if _, err := f.Write([]byte(content)); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

// isNotExist checks whether an SFTP error indicates "file not found".
func isNotExist(err error) bool {
	var statusErr *sftp.StatusError
	if errors.As(err, &statusErr) {
		return statusErr.FxCode() == sftp.ErrSSHFxNoSuchFile
	}
	return false
}

// sensitivePatterns matches credential-like fields for sanitization.
// The value group matches to the end of the line so multi-word
// secrets like "password: my secret" are fully redacted.
var sensitivePatterns = regexp.MustCompile(
	`(?im)(token|password|key|secret|credential)([^\S\n]*[:=][^\S\n]*)(.+)`,
)

// SanitizeDiff redacts sensitive values from diff output.
func SanitizeDiff(diff string) string {
	return sensitivePatterns.ReplaceAllString(diff, "${1}${2}[REDACTED]")
}
