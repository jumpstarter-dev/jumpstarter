package runner

import (
	"fmt"
	"time"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *ExpectStep) run(device harness.Device) StepResult {

	console, err := device.Console()
	if err != nil {
		return StepResult{
			status: Fatal,
			err:    fmt.Errorf("Expect:run(%q) opening console: %w", t.This, err),
		}
	}

	console.SetReadTimeout(time.Second)

	startTime := time.Now()
	expected := t.This
	timeout := float64(t.Timeout)
	p := 0
	received := ""
	buf := make([]byte, 1)

	for p < len(expected) {
		n, err := console.Read(buf)
		if err != nil {
			return StepResult{
				status: Fatal,
				err:    fmt.Errorf("Expect:run(%q) reading %w", expected, err),
			}
		}
		if n == 0 {
			if time.Since(startTime).Seconds() > timeout {
				color.Set(color.FgRed)
				fmt.Printf("\n\nexpecting %q and timed out after %d seconds.\n", expected, t.Timeout)
				color.Unset()

				return StepResult{
					status: Fatal,
					err:    fmt.Errorf("Expect:run(%q) timeout", expected),
				}
			}
			continue
		}
		c := buf[0]
		if t.Echo {
			if c != '\x1b' || !t.DebugEscapes {
				fmt.Print(string(c))
			} else {
				fmt.Print("\n<ESC>")
			}

		}
		received += string(c)
		if c == expected[p] {
			p++
		} else {
			if c != expected[0] {
				p = 0
			} else {
				p = 1
			}
		}
	}

	fmt.Println("")

	return StepResult{
		status: SilentOk,
		err:    nil,
	}
}
