#!/bin/bash
set -eu

# accepted parameters are:
# $1: driver name
# $2: driver class
# $3: author name
# $4: author email

# check if the number of parameters is correct
if [ "$#" -ne 4 ]; then
    echo "Illegal number of parameters"
    echo "Usage: create_driver.sh <driver_name> <driver_class> <author_name> <author_email>"
    echo "Example: create_driver.sh mydriver MyDriver \"John Something\" john@somewhere.com"
    exit 1
fi

export DRIVER_NAME=$1
export DRIVER_CLASS=$2
export AUTHOR_NAME=$3
export AUTHOR_EMAIL=$4

# create the driver directory
DRIVER_DIRECTORY=packages/jumpstarter-driver-${DRIVER_NAME}
MODULE_DIRECTORY=${DRIVER_DIRECTORY}/jumpstarter_driver_${DRIVER_NAME}
# create the module directories
mkdir -p ${MODULE_DIRECTORY}
mkdir -p ${DRIVER_DIRECTORY}/examples

# Create documentation file
DOCS_DIRECTORY=docs/source/api-reference/drivers
DOC_FILE=${DOCS_DIRECTORY}/${DRIVER_NAME}.md

# Create initial documentation file if it doesn't exist
if [ ! -f "${DOC_FILE}" ]; then
    echo "Creating initial documentation file: ${DOC_FILE}"
    cat > "${DOC_FILE}" << EOF
# ${DRIVER_CLASS} Driver

\`jumpstarter-driver-${DRIVER_NAME}\` provides functionality for interacting with ${DRIVER_NAME} devices.

## Installation

```bash
pip install jumpstarter-driver-${DRIVER_NAME}
```

## Configuration

Example configuration:

```yaml
interfaces:
  ${DRIVER_NAME}:
    driver: jumpstarter_driver_${DRIVER_NAME}.${DRIVER_CLASS}Driver
    parameters:
      # Add required parameters here
```

## API Reference

Add API documentation here.
EOF
fi

for f in __init__.py client.py driver_test.py driver.py; do
    echo "Creating: ${MODULE_DIRECTORY}/${f}"
    envsubst < __templates__/driver/jumpstarter_driver/${f}.tmpl > ${MODULE_DIRECTORY}/${f}
done

for f in .gitignore pyproject.toml examples/exporter.yaml; do
    echo "Creating: ${DRIVER_DIRECTORY}/${f}"
    envsubst < __templates__/driver/${f}.tmpl > ${DRIVER_DIRECTORY}/${f}
done

# Create symlink to documentation file instead of README.md
echo "Creating symlink to documentation file"
rel_path=$(realpath --relative-to="${DRIVER_DIRECTORY}" "${DOC_FILE}")
ln -sf "${rel_path}" "${DRIVER_DIRECTORY}/README.md"
echo "Created symlink: ${DRIVER_DIRECTORY}/README.md -> ${rel_path}"
