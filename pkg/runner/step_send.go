package runner

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func (t *SendStep) run(device harness.Device) StepResult {

	console, err := device.Console()
	if err != nil {
		return StepResult{
			status: Fatal,
			err:    fmt.Errorf("Expect:run(%q) opening console: %w", t.This, err),
		}
	}

	for _, send := range t.This {
		time.Sleep(time.Duration(t.DelayMs) * time.Millisecond)
		converted := sendStringParser(send)
		console.Write([]byte(converted))
		color.Set(color.FgYellow)
		fmt.Println("\n\nsent:", send)
		color.Unset()

		if t.Echo {
			monitorOutput(console, t.DebugEscapes)
		}
	}

	fmt.Println("")

	return StepResult{
		status: SilentOk,
		err:    nil,
	}
}

func monitorOutput(console harness.ConsoleInterface, debugEscapes bool) {
	console.SetReadTimeout(time.Millisecond * 100)
	buf := make([]byte, 512)
	for {
		n, err := console.Read(buf)
		if err != nil || n == 0 {
			return
		}
		bufStr := string(buf[:n])

		if debugEscapes {
			bufStr = strings.ReplaceAll(bufStr, "\x1b", "\n<ESC>")
		}
		fmt.Print(bufStr)
	}
}

var replaceMap map[string]string = map[string]string{
	"<ESC>":       "\x1b",
	"<F1>":        "\x1bOP",
	"<F2>":        "\x1bOQ",
	"<F3>":        "\x1bOR",
	"<F4>":        "\x1bOS",
	"<F5>":        "\x1b[15~",
	"<F6>":        "\x1b[17~",
	"<F7>":        "\x1b[18~",
	"<F8>":        "\x1b[19~",
	"<F9>":        "\x1b[20~",
	"<F10>":       "\x1b[21~",
	"<F11>":       "\x1b[23~",
	"<UP>":        "\x1b[A",
	"<DOWN>":      "\x1b[B",
	"<LEFT>":      "\x1b[D",
	"<RIGHT>":     "\x1b[C",
	"<ENTER>":     "\r\n",
	"<TAB>":       "\t",
	"<BACKSPACE>": "\x7f",
	"<DELETE>":    "\x1b[3~",
	"<CTRL-A>":    "\x01",
	"<CTRL-B>":    "\x02",
	"<CTRL-C>":    "\x03",
	"<CTRL-D>":    "\x04",
	"<CTRL-E>":    "\x05",
	"<CTRL-X>":    "\x18"}

func sendStringParser(send string) string {
	for k, v := range replaceMap {
		replace_insensitive := regexp.MustCompile("(?i)" + k)
		send = replace_insensitive.ReplaceAllString(send, v)
	}
	return send
}
