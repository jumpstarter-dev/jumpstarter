# Research: StorageMux Auto-Detect Compression

## Existing Flasher Detection Logic

**Decision**: Locate and reuse the Flasher driver's compression detection logic.

**Rationale**: The Flasher driver already auto-detects .xz/.gz from URL extensions. The same logic should be shared with StorageMux drivers to ensure consistent behavior.

**Alternatives considered**:
- Duplicate the logic in each driver: Violates DRY.
- Content-type sniffing: Unreliable for presigned URLs and local files.

## Extension-to-Compression Mapping

**Decision**: Map extensions to compression formats: `.xz` -> xz, `.gz` -> gzip, `.bz2` -> bzip2, `.zst` -> zstd.

**Rationale**: These are the standard compression extensions used in Linux image distribution.

**Alternatives considered**:
- Magic number detection: More robust but requires reading file headers, adds complexity.

## URL Query Parameter Handling

**Decision**: Strip query parameters before extension detection using `urllib.parse.urlparse`.

**Rationale**: Presigned URLs (AWS S3, etc.) have parameters like `?Expires=...&Signature=...` that would break extension detection.

**Alternatives considered**: None -- this is the standard approach.
