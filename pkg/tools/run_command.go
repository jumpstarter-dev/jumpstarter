package tools

import (
	"fmt"
	"strings"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func RunCommand(console harness.ConsoleInterface, cmd string, wait int) (string, error) {
	buf := make([]byte, 1024)
	// clear the input buffer first, we loop because some devices will only provide
	// a few bytes at a time (i.e. 64)
	for {
		console.SetReadTimeout(100 * time.Millisecond)
		n, err := console.Read(buf)
		if err != nil {
			return "", fmt.Errorf("runCommand %s, clearing input buffer: %w", cmd, err)
		}
		if n == 0 {
			break
		}
	}

	if _, err := console.Write([]byte(cmd + "\n")); err != nil {
		return "", fmt.Errorf("runCommand %s, sending command: %w", cmd, err)
	}

	time.Sleep(time.Duration(wait) * time.Second)
	n, err := console.Read(buf)
	if err != nil {
		return "", fmt.Errorf("runCommand %s, reading response: %w", cmd, err)
	}

	all := string(buf[:n])
	lines := strings.Split(all, "\n")
	if len(lines) > 1 {
		// the first line is the command we just sent, so we skip it
		return strings.Join(lines[1:], "\n"), nil
	} else {
		return "", nil
	}
}
