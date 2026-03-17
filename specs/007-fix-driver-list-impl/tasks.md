# Tasks: Fix Driver List (007-fix-driver-list)

**Input**: Design documents from `/specs/007-fix-driver-list/`
**Prerequisites**: research.md (audit results), data-model.md (entry-point mappings)

## Phase 1: Audit

- [x] T001 Audit all driver packages for missing `[project.entry-points."jumpstarter.drivers"]` (see research.md for findings: 15 packages missing entry-points)

---

## Phase 2: Add Missing Entry-Points

**Purpose**: Add `[project.entry-points."jumpstarter.drivers"]` to each affected pyproject.toml

- [x] T002 [P] Add entry-points to `python/packages/jumpstarter-driver-ble/pyproject.toml` (BleWriteNotifyStream)
- [x] T003 [P] Add entry-points to `python/packages/jumpstarter-driver-flashers/pyproject.toml` (TIJ784S4Flasher, RCarS4Flasher)
- [x] T004 [P] Add entry-points to `python/packages/jumpstarter-driver-http/pyproject.toml` (HttpServer)
- [x] T005 [P] Add entry-points to `python/packages/jumpstarter-driver-http-power/pyproject.toml` (HttpPower)
- [x] T006 [P] Add entry-points to `python/packages/jumpstarter-driver-iscsi/pyproject.toml` (ISCSI)
- [x] T007 [P] Add entry-points to `python/packages/jumpstarter-driver-probe-rs/pyproject.toml` (ProbeRs)
- [x] T008 [P] Add entry-points to `python/packages/jumpstarter-driver-pyserial/pyproject.toml` (PySerial)
- [x] T009 [P] Add entry-points to `python/packages/jumpstarter-driver-qemu/pyproject.toml` (QemuFlasher, QemuPower, Qemu)
- [x] T010 [P] Add entry-points to `python/packages/jumpstarter-driver-ridesx/pyproject.toml` (RideSXDriver, RideSXPowerDriver)
- [x] T011 [P] Add entry-points to `python/packages/jumpstarter-driver-snmp/pyproject.toml` (SNMPServer)
- [x] T012 [P] Add entry-points to `python/packages/jumpstarter-driver-ssh/pyproject.toml` (SSHWrapper)
- [x] T013 [P] Add entry-points to `python/packages/jumpstarter-driver-tftp/pyproject.toml` (Tftp)
- [x] T014 [P] Add entry-points to `python/packages/jumpstarter-driver-tmt/pyproject.toml` (TMT)
- [x] T015 [P] Add entry-points to `python/packages/jumpstarter-driver-uboot/pyproject.toml` (UbootConsole)
- [x] T016 [P] Add entry-points to `python/packages/jumpstarter-driver-ustreamer/pyproject.toml` (UStreamer)

---

## Phase 3: Verification

- [x] T017 Verify all driver pyproject.toml files have `[project.entry-points."jumpstarter.drivers"]` (except opendal and uds which are intentionally excluded)

---

## Dependencies and Execution Order

- T001 is informational (uses research.md findings)
- T002-T016 are all independent and can run in parallel
- T017 depends on T002-T016 completion

## Notes

- opendal uses `jumpstarter.adapters` group (not a driver)
- uds is an abstract interface only (concrete drivers are in uds-can and uds-doip)
- BaseFlasher in flashers package is abstract; only TIJ784S4Flasher and RCarS4Flasher are registered
