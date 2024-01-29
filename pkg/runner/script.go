package runner

import (
	"fmt"
	"strings"

	"github.com/creasty/defaults"
)

// yaml parser

type JumpstarterScript struct {
	Name          string            `yaml:"name"`
	Selector      []string          `yaml:"selector"`
	Timeout       uint              `default:"1800" yaml:"timeout"`
	Drivers       []string          `yaml:"drivers"`
	ExpectTimeout uint              `default:"120" yaml:"expect-timeout"`
	Steps         []JumpstarterStep `yaml:"steps"`
	Cleanup       []JumpstarterStep `yaml:"cleanup"`
}

func (e *JumpstarterScript) UnmarshalYAML(unmarshal func(interface{}) error) error {
	// Set the defaults defined in struct metadata
	defaults.Set(e)
	type plain JumpstarterScript
	if err := unmarshal((*plain)(e)); err != nil {
		return err
	}
	return nil
}

type JumpstarterStep struct {
	// name of the task
	Name                  string                     `yaml:"name"`
	SetDiskImage          *SetDiskImageStep          `yaml:"set-disk-image,omitempty"`
	Expect                *ExpectStep                `yaml:"expect,omitempty"`
	Send                  *SendStep                  `yaml:"send,omitempty"`
	Storage               *StorageStep               `yaml:"storage,omitempty"`
	Power                 *PowerStep                 `yaml:"power,omitempty"`
	Reset                 *ResetStep                 `yaml:"reset,omitempty"`
	Pause                 *PauseStep                 `yaml:"pause,omitempty"`
	Comment               *CommentStep               `yaml:"comment,omitempty"`
	WriteAnsibleInventory *WriteAnsibleInventoryStep `yaml:"write-ansible-inventory,omitempty"`
	LocalShell            *LocalShellStep            `yaml:"local-shell,omitempty"`
	parent                *JumpstarterScript
}

type SetDiskImageStep struct {
	Image         string `yaml:"image"`
	AttachStorage bool   `yaml:"attach_storage"`
	OffsetGB      uint   `yaml:"offset-gb"`
}

type ExpectStep struct {
	This         string `yaml:"this"`
	Fatal        string `yaml:"fatal"`
	Echo         bool   `default:"true" yaml:"echo"`
	DebugEscapes bool   `default:"false" yaml:"debug_escapes"`
	Timeout      uint   `yaml:"timeout"`
}

func (e *ExpectStep) UnmarshalYAML(unmarshal func(interface{}) error) error {
	defaults.Set(e)
	type plain ExpectStep
	if err := unmarshal((*plain)(e)); err != nil {
		return err
	}

	return nil
}

type ResetStep struct {
	TimeMs uint `yaml:"time_ms"`
}

type PauseStep uint

type WriteAnsibleInventoryStep struct {
	Filename string `default:"inventory" yaml:"filename"`
	User     string `default:"root" yaml:"user"`
	SshKey   string `yaml:"ssh_key"`
}

func (e *WriteAnsibleInventoryStep) UnmarshalYAML(unmarshal func(interface{}) error) error {
	defaults.Set(e)
	type plain WriteAnsibleInventoryStep
	if err := unmarshal((*plain)(e)); err != nil {
		return err
	}

	return nil
}

type LocalShellStep struct {
	Script string `yaml:"script"`
}

type SendStep struct {
	This         []string `yaml:"this"`
	DelayMs      uint     `default:"100" yaml:"delay_ms"`
	Echo         bool     `default:"true" yaml:"echo"`
	DebugEscapes bool     `default:"false" yaml:"debug_escapes"`
}

func (s *SendStep) UnmarshalYAML(unmarshal func(interface{}) error) error {
	defaults.Set(s)
	type plain SendStep
	if err := unmarshal((*plain)(s)); err != nil {
		return err
	}

	return nil
}

type StorageStep string
type PowerStep string
type CommentStep string

// a type enum with changed, ok, error
type TaskStatus int

const (
	Done TaskStatus = iota
	SilentOk
	Fatal
)

type StepResult struct {
	status TaskStatus
	err    error
}

func (p *JumpstarterStep) getName() string {
	if p.Name != "" {
		return p.Name
	}

	switch {
	case p.SetDiskImage != nil:
		return fmt.Sprintf("set-disk-image: %v", p.SetDiskImage.Image)
	case p.Expect != nil:
		return fmt.Sprintf("expect: %q", p.Expect.This) // we should add a getName method instead
	case p.Send != nil:
		return fmt.Sprintf("send: %v", strings.Replace(strings.Join(p.Send.This, ", "), "\n", "", -1))
	case p.Storage != nil:
		return fmt.Sprintf("storage: %q", string(*p.Storage))
	case p.Power != nil:
		return fmt.Sprintf("power: %q", string(*p.Power))
	case p.Reset != nil:
		return "reset"
	case p.Pause != nil:
		return fmt.Sprintf("pause: %d", *p.Pause)
	case p.WriteAnsibleInventory != nil:
		return "write-ansible-inventory"
	case p.LocalShell != nil:
		return "local-shell"
	case p.Comment != nil:
		return string(*p.Comment)
	default:
		return "unknown"
	}
}
