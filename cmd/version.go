package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var VERSION string

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Show the version of the jumpstarter cli tool",

	Run: func(cmd *cobra.Command, args []string) {
		fmt.Println("Jumpstarter CLI Version: \t", VERSION)
	},
}

func init() {
	rootCmd.AddCommand(versionCmd)
}
