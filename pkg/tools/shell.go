package tools

import (
	"io"
	"os/exec"
	"strings"
)

func RunBash(script string, stdout, stderr io.Writer) error {
	cmd := exec.Command("sh")
	cmd.Stdin = strings.NewReader(script + "\n")
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	return cmd.Run()
}
