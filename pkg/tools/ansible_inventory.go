package tools

import (
	"fmt"
	"os"
	"regexp"

	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
)

func CreateAnsibleInventory(device harness.Device, output *os.File, user string, ssh_key string) error {
	serial, err := device.Console()
	if err != nil {
		return fmt.Errorf("CreateAnsibleInventory: error getting console: %w", err)
	}

	result, err := RunCommand(serial, "ip a show dev eth0", 1)
	if err != nil {
		return fmt.Errorf("CreateAnsibleInventory: error requesting IP address: %w", err)
	}

	ip, err := extractSrcIPAddress(result)
	if err != nil {
		return fmt.Errorf("CreateAnsibleInventory: error parsing IP address: %w", err)
	}

	fmt.Fprint(output, "---\nboards:\n  hosts:\n")
	fmt.Fprintf(output, "    %s:\n", device.Name())
	fmt.Fprintf(output, "      ansible_host: %s\n", ip)
	fmt.Fprintf(output, "      ansible_user: %s\n", user)
	fmt.Fprintf(output, "      ansible_become: yes\n")
	fmt.Fprintf(output, "      ansible_ssh_common_args: '-o StrictHostKeyChecking=no'\n")
	if ssh_key != "" {
		fmt.Fprintf(output, "      ansible_ssh_private_key_file: %s\n", ssh_key)
	}
	return nil
}

func extractSrcIPAddress(input string) (string, error) {
	re := regexp.MustCompile(`inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})`)
	matches := re.FindStringSubmatch(input)
	if len(matches) > 1 {
		return matches[1], nil
	} else {
		return "", fmt.Errorf("No src IP address found in %q", input)
	}
}
