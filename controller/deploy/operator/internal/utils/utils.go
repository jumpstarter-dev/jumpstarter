package utils

import "maps"

// MergeMaps merges two string maps, with values from the second map taking precedence.
// This is useful for merging labels, annotations, or any other string key-value pairs.
func MergeMaps(base, overrides map[string]string) map[string]string {
	merged := make(map[string]string)

	// Add base map first
	maps.Copy(merged, base)

	// Override with values from second map
	maps.Copy(merged, overrides)

	return merged
}
