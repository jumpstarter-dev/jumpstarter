#!/usr/bin/env bash
# Renders a file-based OLM catalog in which CHANNEL holds one bundle,
# BUNDLE_IMG. The entry's skipRange covers every lower version, so OLM can
# upgrade to it from whatever is installed, including a release from another
# channel. The package name and version come from the bundle itself.
#
# Usage: OPM=path/to/opm render-catalog.sh BUNDLE_IMG CHANNEL OUTPUT_DIR
set -euo pipefail

bundle_img=$1
channel=$2
out=$3

bundle=$("${OPM}" render "${bundle_img}" -o json)
package=$(jq -r .package <<<"${bundle}")
name=$(jq -r .name <<<"${bundle}")
version=$(jq -r '.properties[] | select(.type == "olm.package") | .value.version' <<<"${bundle}")

mkdir -p "${out}/${package}"
{
	jq -n --arg package "${package}" --arg channel "${channel}" \
		'{schema: "olm.package", name: $package, defaultChannel: $channel}'
	jq -n --arg package "${package}" --arg channel "${channel}" --arg name "${name}" --arg range ">=0.0.0 <${version}" \
		'{schema: "olm.channel", package: $package, name: $channel, entries: [{name: $name, skipRange: $range}]}'
	printf '%s\n' "${bundle}"
} >"${out}/${package}/catalog.json"

"${OPM}" validate "${out}"
echo "Rendered ${name} as the only entry of channel ${channel} in ${out}"
