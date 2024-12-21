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
DRIVER_DIRECTORY=contrib/drivers/${DRIVER_NAME}
MODULE_DIRECTORY=${DRIVER_DIRECTORY}/jumpstarter_driver_${DRIVER_NAME}
# create the module directories
mkdir -p ${MODULE_DIRECTORY}
mkdir -p ${DRIVER_DIRECTORY}/examples


for f in __init__.py client.py driver_test.py driver.py; do
    echo "Creating: ${MODULE_DIRECTORY}/${f}"
    envsubst < contrib/__templates__/driver/jumpstarter_driver/${f}.tmpl > ${MODULE_DIRECTORY}/${f}
done

for f in .gitignore pyproject.toml README.md examples/exporter.yaml; do
    echo "Creating: ${DRIVER_DIRECTORY}/${f}"
    envsubst < contrib/__templates__/driver/${f}.tmpl > ${DRIVER_DIRECTORY}/${f}
done

