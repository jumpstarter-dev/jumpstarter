package runner

import (
	"fmt"
	"math/rand"
	"os"

	"github.com/fatih/color"
	"github.com/jumpstarter-dev/jumpstarter/pkg/harness"
	"gopkg.in/yaml.v3"
)

func RunScript(device_id, driver, yaml_file string, disableCleanup bool) error {

	// parse yaml file into a JumpstarterScript struct
	script := JumpstarterScript{}

	// read yaml file
	if err := readScript(yaml_file, &script); err != nil {
		return fmt.Errorf("RunScript: %w", err)
	}
	// TODO: check if the yaml contents are consistent

	var device harness.Device

	// TODO implement retry/wait
	//      sometimes devices are busy or can happen fail due to a race condition
	device, err := script.getDevice(device_id, driver)
	if err != nil {
		return fmt.Errorf("RunScript: %w", err)
	}
	color.Set(color.FgHiYellow)
	fmt.Printf("⚙ Using device %q with tags %v\n", device.Name(), device.Tags())
	color.Unset()

	return script.run(device, disableCleanup)
}

func (p *JumpstarterStep) run(device harness.Device) StepResult {
	if p.Comment == nil {
		printHeader("Step", p.getName())
	}

	switch {
	case p.Comment != nil:
		return p.Comment.run(device)

	case p.SetDiskImage != nil:
		return p.SetDiskImage.run(device)

	case p.Expect != nil:
		if p.Expect.Timeout == 0 {
			p.Expect.Timeout = uint(p.parent.ExpectTimeout)
		}
		return p.Expect.run(device)

	case p.Send != nil:
		return p.Send.run(device)

	case p.Storage != nil:
		return p.Storage.run(device)

	case p.Power != nil:
		return p.Power.run(device)

	case p.Reset != nil:
		return p.Reset.run(device)

	case p.Pause != nil:
		return p.Pause.run(device)

	case p.WriteAnsibleInventory != nil:
		return p.WriteAnsibleInventory.run(device)

	case p.LocalShell != nil:
		return p.LocalShell.run(device)
	}

	return StepResult{
		status: Fatal,
		err:    fmt.Errorf("invalid task: %s", p.getName()),
	}
}

func (p *JumpstarterScript) getDevice(device_id string, driver string) (harness.Device, error) {
	if device_id != "" {
		device, err := harness.FindDevice(driver, device_id)
		if err != nil {
			return nil, fmt.Errorf("getDevice: %w", err)
		}
		return device, nil
	} else {

		devices, err := harness.FindDevices(driver, p.Selector)
		if err != nil {
			return nil, fmt.Errorf("getDevice: %w", err)
		}

		nonBusy := filterOutBusy(devices)

		if len(devices) == 0 {
			return nil, fmt.Errorf("getDevice: no devices found")
		}

		if len(nonBusy) == 0 {

			return nil, fmt.Errorf("getDevice: all devices are busy")
		}

		device := nonBusy[rand.Intn(len(nonBusy))]
		if err := device.Lock(); err != nil {

			return nil, fmt.Errorf("getDevice: tried to open a device: %w", err)
		}
		return device, nil
	}
}

func (p *JumpstarterScript) runScriptSteps(device harness.Device) error {
	return p.runTasks(&(p.Steps), device)
}

func (p *JumpstarterScript) runScriptCleanup(device harness.Device) error {
	printHeader("Cleanup", p.Name)
	return p.runTasks(&(p.Cleanup), device)
}

func (p *JumpstarterScript) run(device harness.Device, disableCleanup bool) error {
	var errCleanup error
	errTasks := p.runScriptSteps(device)

	if disableCleanup {
		color.Set(color.FgHiYellow)
		fmt.Printf("⚠ Cleaning phase has been skipped based on the request")
		color.Unset()
	} else {
		errCleanup = p.runScriptCleanup(device)
	}
	if errCleanup != nil {
		if errTasks != nil {
			return fmt.Errorf("errors during script run %w and cleanup: %w", errTasks, errCleanup)
		} else {
			return fmt.Errorf("errors during script cleanup: %w", errCleanup)
		}
	}
	if errTasks != nil {
		return fmt.Errorf("errors during script run: %w", errTasks)
	}
	return nil
}

func (p *JumpstarterScript) runTasks(steps *[]JumpstarterStep, device harness.Device) error {

	for _, task := range *steps {
		task.parent = p // The yaml parser does not do this, but we do it here
		res := task.run(device)
		switch res.status {
		case SilentOk:

		case Done:
			color.Set(color.FgHiGreen)
			fmt.Printf("[✓] done\n\n")
			color.Unset()
		case Fatal:
			color.Set(color.FgHiRed)
			fmt.Printf("[x] failed\n\n")
			color.Unset()
			return fmt.Errorf("runTasks: %w", res.err)
		}
	}
	return nil
}

func filterOutBusy(devices []harness.Device) []harness.Device {
	var freeDevices []harness.Device
	for _, device := range devices {
		if busy, _ := device.IsBusy(); !busy {
			freeDevices = append(freeDevices, device)
		}
	}
	return freeDevices
}

func readScript(yaml_file string, script *JumpstarterScript) error {
	script_data, err := os.ReadFile(yaml_file)
	if err != nil {
		return fmt.Errorf("readScript(%q): Error reading yaml file: %w", yaml_file, err)
	}

	if err := yaml.Unmarshal([]byte(script_data), &script); err != nil {
		return fmt.Errorf("readScript(%q): %w", yaml_file, err)
	}
	return nil
}

func printHeader(header, name string) {
	fmt.Println(getHeader(header, name))
}

func getHeader(header, name string) string {
	return fmt.Sprintf("➤ %s ➤ %s", header, name)
}
