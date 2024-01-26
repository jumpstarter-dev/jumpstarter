package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

const VERSION = "0.3.2"

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Show the version of the jumpstarter cli tool",

	Run: func(cmd *cobra.Command, args []string) {
		fmt.Println("Jumpstarter CLI Version: " + VERSION)
	},
}

func init() {
	rootCmd.AddCommand(versionCmd)
}
