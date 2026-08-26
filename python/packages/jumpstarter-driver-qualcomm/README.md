# jumpstarter-driver-qualcomm

Jumpstarter driver package for Qualcomm automotive SoCs (SA8775P, SA8650P, and related platforms).

## Installation

```console
$ pip3 install jumpstarter-driver-qualcomm
```

## Configuration

### Driver

| Driver            | Description                                                         |
| ----------------- | ------------------------------------------------------------------- |
| `QualcommFlasher` | QDL/fastboot flashing, mode control, and firmware identification |

**driver**: `jumpstarter_driver_qualcomm.driver.QualcommFlasher`

TAC serial handles power on/off and mode switching (EDL/fastboot). No separate power driver is required.

Example exporter configuration:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: jumpstarter-lab
  name: qualcomm-sa8775p
endpoint: <endpoint>
token: <token>
export:
  firmware:
    type: "jumpstarter_driver_qualcomm.driver.QualcommFlasher"
    config:
      soc_type: sa8775p
      work_dir: /var/lib/jumpstarter/qualcomm
      board_revision: v3
      power_cycle_delay: 2.0
    children:
      tac:
        ref: tac
      serial:
        ref: serial
      sail:
        ref: sail
  tac:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyACM0"
      baudrate: 115200
  serial:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB1"
      baudrate: 115200
  sail:
    type: "jumpstarter_driver_pyserial.driver.PySerial"
    config:
      url: "/dev/ttyUSB2"
      baudrate: 115200
```

#### Children

| Child driver | Description                                      | Required for flash | Required for `id` |
| ------------ | ------------------------------------------------ | ------------------ | ----------------- |
| tac          | TAC serial for power and mode control            | Yes                | Yes               |
| serial       | Main boot serial console                         | No                 | Yes               |
| sail         | SAIL boot serial console                         | No                 | Yes               |

### Config parameters

| Parameter         | Description                                              | Type  | Required | Default                       |
| ----------------- | -------------------------------------------------------- | ----- | -------- | ----------------------------- |
| soc_type          | SoC profile for TAC GPIO sequences (`sa8775p`, `sa8540p1`, `sa8540p2`) | str   | no       | sa8775p                       |
| work_dir          | Base directory for firmware extraction and optional `--cached` storage | str   | no       | /var/lib/jumpstarter/qualcomm |
| board_revision    | Board revision for CDT image selection (`v1`, `v2`, `v3`, `v4`) | str   | no       |                               |
| qdl_timeout       | Timeout for QDL subprocess steps (seconds)               | int   | no       | 1800                          |
| fastboot_timeout  | Timeout for fastboot subprocess steps (seconds)          | int   | no       | 600                           |
| power_cycle_delay | Delay between power off and on during identification (seconds) | float | no       | 2.0                           |

## Usage

### CLI

```bash
j firmware flash ./sx4-r00021.1a.tar.xz
j firmware flash ./sx4-r00021.1a.tar.xz --manifest ./es22.yaml
j firmware flash ./sx4-r00021.1a.tar.xz --manifest ./es22.yaml --cached
j firmware flash --manifest ./es22.yaml --cached
j firmware id -v
j firmware id --capture ./boot-logs
j firmware boot-to-edl
j firmware boot-to-fastboot
```

Use `--cached` to keep the extracted firmware under `work_dir/<manifest.data.folder>` on the exporter after a successful flash. Subsequent runs with the same manifest skip download and extraction, which is useful for lease-end hooks that restore a known firmware version without repeating the full transfer.

## Firmware archive (`.tar.xz`)

Firmware is distributed as a compressed tar archive. The driver accepts local paths, URLs, and other Jumpstarter file sources. Archives may be `.tar`, `.tar.xz`, `.tar.gz`, or other formats supported by the streaming decompressor.

### Layout

Paths in the manifest are relative to `data.folder` after extraction. A typical archive looks like this:

```
firmware.tar.xz
├── jumpstarter_manifest.yaml   # optional embedded manifest
├── ufs/
│   ├── prog_firehose_ddr.elf
│   ├── provision_default.xml
│   ├── rawprogram0.xml
│   └── patch0.xml
├── spinor/
│   ├── prog_firehose_ddr.elf
│   └── rawprogram0.xml
├── abl/                        # optional, referenced by data.abl_image
│   └── sa8775p_abl_signed.elf
└── cdt/                        # optional, referenced by data.cdt_image.*
    └── LEMANSAU_QAM_1.2.0.bin
```

### Extraction behavior

| `data.extract_to_folder` | Archive contents | Extracted to |
| ------------------------ | ---------------- | ------------ |
| `false` (default)        | Top-level folder matching `data.folder` | `{work_dir}/{folder}/...` |
| `true`                   | Flat layout (no top-level folder) | `{work_dir}/{folder}/...` |

Use `extract_to_folder: true` when the vendor tarball unpacks directly into `ufs/`, `spinor/`, and similar directories instead of a single release directory.

When no `--manifest` is passed on the CLI, the driver looks inside the extracted archive for `jumpstarter_manifest.yaml`. If the manifest is only inside the archive, it is read before extraction so `extract_to_folder` is applied correctly.

## Manifest format

The manifest is a YAML file describing firmware metadata and an ordered list of typed flash steps. Pass it with `--manifest`, embed it in the archive, or both (`--manifest` takes precedence).

Reference manifests live in `jumpstarter_driver_qualcomm/examples/manifests/`.

### Top-level fields

| Field         | Description                                      | Required |
| ------------- | ------------------------------------------------ | -------- |
| `name`        | Human-readable firmware name                     | yes      |
| `description` | Optional description                             | no       |
| `data`        | Firmware layout and post-flash image paths       | yes      |
| `steps`       | Ordered list of flash steps                      | yes      |

### `data` fields

| Field               | Description                                                                 | Required | Default |
| ------------------- | --------------------------------------------------------------------------- | -------- | ------- |
| `folder`            | Directory name under the extraction root containing firmware files          | yes      |         |
| `extract_to_folder` | Extract archive contents directly into `folder/` instead of preserving paths | no       | `false` |
| `abl_image`         | Relative path to ABL ELF flashed via fastboot after the steps                | no       |         |
| `cdt_image`         | Free-form map of board revision keys to CDT binary paths (`v1`, `v2`, `v2.5`, `v5`, …) | no       |         |

CDT image selection uses `board_revision` from the exporter config. Keys are normalized to lowercase `v`-prefixed strings (`2.5` becomes `v2.5`). CS4/CS5 manifests default to `v4`, then `v3`, when `board_revision` is not set. `v2.5` falls back to `v2` when no dedicated entry exists.

```yaml
cdt_image:
  v1: "cdt/LEMANSAU_QAM_1.1.0.bin"
  v2: "cdt/LEMANSAU_QAM_1.1.0.bin"
  v3: "cdt/LEMANSAU_QAM_1.2.0.bin"
  v4: "cdt/LEMANSAU_QAM_1.2.0.bin"
```

### Step types

Each step is a mapping with exactly one action key. All steps support optional `name` and `retry_mode` (`edl` or `fastboot`).

#### `set_mode`

Boot the device into EDL or fastboot using the TAC serial profile.

```yaml
- set_mode: edl
  check_dmesg: "USB QTI_HS"
```

| Field         | Description                                      |
| ------------- | ------------------------------------------------ |
| `set_mode`    | `edl` or `fastboot`                              |
| `check_dmesg` | Optional dmesg substring that must appear after the mode change |

#### `sleep`

Pause between steps.

```yaml
- sleep: 5
```

#### `qdl`

Run the Qualcomm `qdl` tool against files under the firmware tree.

```yaml
- name: "Flash UFS"
  retry_mode: edl
  qdl:
    storage: ufs
    programmer: prog_firehose_ddr.elf
    files:
      - "rawprogram*.xml"
      - "patch*.xml"
    workdir: ufs
```

| Field        | Description                                                                 |
| ------------ | --------------------------------------------------------------------------- |
| `storage`    | `ufs` or `spinor`                                                           |
| `programmer` | Firehose programmer ELF, relative to the QDL workdir                        |
| `files`      | XML paths or globs relative to the QDL workdir                              |
| `workdir`    | Directory relative to `data.folder`; defaults to the value of `storage`     |

#### `fastboot`

Run fastboot erase/flash/continue operations after the device is in fastboot mode.

```yaml
- name: "Flash perf hypervisor"
  retry_mode: fastboot
  fastboot:
    erase:
      - hyp_a
      - hyp_b
    flash:
      - partition: hyp_a
        file: ufs/hypvmperformance.mbn
      - partition: hyp_b
        file: ufs/hypvmperformance.mbn
    continue: true
```

| Field      | Description                                           |
| ---------- | ----------------------------------------------------- |
| `erase`    | Optional list of partitions to erase                  |
| `flash`    | Optional list of `{partition, file}` entries          |
| `continue` | Run `fastboot continue` after flashing                |

### Example manifest

```yaml
name: "SA8775P ES22 AWE Firmware"
description: "ES22 AWE firmware update package (QDL)"
data:
  folder: "r00002.2a_AWE"
  extract_to_folder: true
  abl_image: "abl/mar_2026_mkorpershoek/sa8775p_es21_abl_signed.elf"
  cdt_image:
    v1: "cdt/LEMANSAU_QAM_1.1.0.bin"
    v2: "cdt/LEMANSAU_QAM_1.1.0.bin"
    v3: "cdt/LEMANSAU_QAM_1.2.0.bin"
    v4: "cdt/LEMANSAU_QAM_1.2.0.bin"
steps:
  - set_mode: edl
    check_dmesg: "USB QTI_HS"

  - name: "UFS Provisioning"
    retry_mode: edl
    qdl:
      storage: ufs
      programmer: prog_firehose_ddr.elf
      files:
        - provision_default.xml

  - sleep: 5

  - name: "Flash UFS"
    retry_mode: edl
    qdl:
      storage: ufs
      programmer: prog_firehose_ddr.elf
      files:
        - "rawprogram*.xml"
        - "patch*.xml"

  - set_mode: fastboot
    check_dmesg: "Product: Android"
```

## Requirements on exporter host

- `qdl` (Qualcomm download tool)
- `fastboot`
- USB access to the DUT in EDL/fastboot modes
- TAC serial device for mode switching
