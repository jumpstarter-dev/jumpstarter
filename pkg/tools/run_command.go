package tools

import (
	"bytes"
	"fmt"
	"strings"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func readUntil(console harness.ConsoleInterface) ([]byte, error) {
	// Read the console in a loop until no more data is produced within the read timeout.
	// Because some devices will only provide a few bytes at a time (i.e. 64),
	// and a single Read call cannot drain the whole command output.
	all := bytes.NewBuffer(nil)
	buf := make([]byte, 1024)
	for {
		n, err := console.Read(buf)
		if err != nil {
			return nil, err
		}
		if n == 0 {
			break
		}
		// not checking as err is always nil for Buffer.Write
		_, _ = all.Write(buf[:n])
	}
	return all.Bytes(), nil
}

func RunCommand(console harness.ConsoleInterface, cmd string, wait int) (string, error) {
	console.SetReadTimeout(100 * time.Millisecond)

	// clear the input buffer first
	_, err := readUntil(console)
	if err != nil {
		return "", fmt.Errorf("runCommand %s, clearing input buffer: %w", cmd, err)
	}

	if _, err := console.Write([]byte(cmd + "\r\n")); err != nil {
		return "", fmt.Errorf("runCommand %s, sending command: %w", cmd, err)
	}

	time.Sleep(time.Duration(wait) * time.Second)

	all, err := readUntil(console)
	if err != nil {
		return "", fmt.Errorf("runCommand %s, reading response: %w", cmd, err)
	}

	lines := strings.Split(string(all), "\n")
	if len(lines) > 1 {
		// the first line is the command we just sent, so we skip it
		return strings.Join(lines[1:], "\n"), nil
	} else {
		return "", nil
	}
}
