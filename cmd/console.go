/*
Copyright © 2023 Miguel Angel Ajo Pelayo <majopela@redhat.com
*/
package cmd

import (
	"fmt"
	"io"
	"os"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"github.com/spf13/cobra"
	"golang.org/x/term"
)

// powerCmd represents the listDevices command
var consoleCmd = &cobra.Command{
	Use:   "console",
	Short: "Console output from a device",

	Run: func(cmd *cobra.Command, args []string) {
		if err := cobra.MinimumNArgs(1)(cmd, args); err != nil {
			handleErrorAsFatal(err)
		}

		driver := cmd.Flag("driver").Value.String()
		device, err := harness.FindDevice(driver, args[0])
		handleErrorAsFatal(err)

		serialConsole(device)
	},
}

func init() {
	rootCmd.AddCommand(consoleCmd)
	consoleCmd.Flags().StringP("driver", "d", "", "Only devices for the specified driver")
}

func serialConsole(device harness.Device) {
	serial, err := device.Console()
	handleErrorAsFatal(err)
	defer serial.Close()

	color.Set(COLOR_CMD_INFO)
	fmt.Println("💻 Entering console: Press Ctrl-B 3 times to exit console")
	color.Unset()
	runConsole(serial)
}

func runConsole(serial io.ReadWriteCloser) {
	for {
		oldState, err := term.MakeRaw(int(os.Stdin.Fd()))
		if err != nil {
			handleErrorAsFatal(err)
		}
		defer func() {
			term.Restore(int(os.Stdin.Fd()), oldState)
			// reset terminal and clear screen
			fmt.Print("\033c\033[2J\033[H")

		}()
		// TODO: this will result in the output of the serial console exit command
		// we need to control console via DTR instead
		go io.Copy(os.Stdout, serial)
		ctrlBCount := 0
		for {
			var b []byte = make([]byte, 1)
			_, err := os.Stdin.Read(b)
			if err != nil {
				handleErrorAsFatal(err)
			}
			if b[0] == 2 {
				ctrlBCount++
				if ctrlBCount == 3 {
					return
				}
			} else {
				ctrlBCount = 0
			}
			serial.Write(b)
		}
	}
}
