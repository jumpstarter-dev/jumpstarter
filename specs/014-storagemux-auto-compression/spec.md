# Feature Specification: StorageMux Auto-Detect Compression

**Feature Branch**: `014-storagemux-auto-compression`
**Created**: 2026-03-17
**Status**: Draft
**Input**: User description: "StorageMux flashing does not auto-detect compression - issue #54"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Flash compressed images without manual flags (Priority: P1)

A user runs `j sdcard flash https://example.com/image.raw.xz` and expects the CLI to detect the `.xz` extension and decompress automatically, just like the Flasher driver does. Currently, the StorageMux driver requires `--compression xz` to be specified manually, which is inconsistent and error-prone.

**Why this priority**: This is a usability bug causing confusion and wasted time. Users flash compressed images frequently.

**Independent Test**: Run `j sdcard flash` with a `.xz` URL without `--compression` and verify the image is decompressed and flashed correctly.

**Acceptance Scenarios**:

1. **Given** a URL ending in `.raw.xz`, **When** user runs `j sdcard flash <url>` without `--compression`, **Then** the image is automatically decompressed with xz.
2. **Given** a URL ending in `.raw.gz`, **When** user runs `j sdcard flash <url>` without `--compression`, **Then** the image is automatically decompressed with gzip.
3. **Given** a URL ending in `.raw` (no compression extension), **When** user runs `j sdcard flash <url>`, **Then** no decompression is applied.
4. **Given** a URL ending in `.raw.xz`, **When** user runs `j sdcard flash <url> --compression none`, **Then** the explicit flag overrides auto-detection and no decompression is applied.

---

### User Story 2 - Consistent behavior across driver types (Priority: P2)

The Flasher driver already auto-detects compression from file extensions. StorageMux drivers (SDWire, SDMux, DUTLink) should behave identically to avoid user confusion.

**Why this priority**: Inconsistent behavior across drivers erodes user trust and increases support burden.

**Independent Test**: Compare the behavior of `j storage flash` (Flasher) and `j sdcard flash` (StorageMux) with the same compressed URL.

**Acceptance Scenarios**:

1. **Given** a compressed image URL, **When** flashed via either Flasher or StorageMux driver, **Then** both auto-detect and decompress identically.

---

### Edge Cases

- What happens with double extensions like `.tar.xz`?
- What about URLs with query parameters after the extension (e.g., `image.raw.xz?token=abc`)?
- What if the extension doesn't match the actual compression format?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: StorageMux drivers MUST auto-detect compression format from the file extension (.xz, .gz, .bz2, .zst).
- **FR-002**: Auto-detection MUST be overridable with an explicit `--compression` flag.
- **FR-003**: When `--compression none` is specified, auto-detection MUST be skipped.
- **FR-004**: URL query parameters MUST be stripped before extension detection.

### Key Entities

- **StorageMux Driver**: Handles SD card flashing via SDWire/SDMux/DUTLink hardware.
- **Compression Format**: One of xz, gz, bz2, zst, or none.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can flash compressed images without specifying `--compression` manually.
- **SC-002**: Auto-detection correctly identifies xz, gz, bz2, and zst from file extensions.
- **SC-003**: Explicit `--compression` flag overrides auto-detection in all cases.
