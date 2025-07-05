#!/bin/bash
set -euxv

# accepted parameters are:
# $1: driver name
# $2: driver class
# $3: author name
# $4: author email

# Function to prompt for input with default value
prompt_with_default() {
    local prompt="$1"
    local default="$2"
    local varname="$3"
    
    if [ -n "$default" ]; then
        echo "$prompt (default: $default):"
        read input
        eval "$varname=\"\${input:-$default}\""
    else
        echo "$prompt:"
        read input
        eval "$varname=\"$input\""
    fi
}

# Handle different parameter scenarios
if [ "$#" -eq 4 ]; then
    # All parameters provided
    export DRIVER_NAME=$1
    export DRIVER_CLASS=$2
    export AUTHOR_NAME=$3
    export AUTHOR_EMAIL=$4
elif [ "$#" -eq 2 ]; then
    # Only driver name and class provided, auto-detect author info
    export DRIVER_NAME=$1
    export DRIVER_CLASS=$2
    
    # Try to get author info from git config
    GIT_NAME=$(git config user.name 2>/dev/null || echo "")
    GIT_EMAIL=$(git config user.email 2>/dev/null || echo "")
    
    prompt_with_default "Author name" "$GIT_NAME" "AUTHOR_NAME"
    prompt_with_default "Author email" "$GIT_EMAIL" "AUTHOR_EMAIL"
    
    export AUTHOR_NAME
    export AUTHOR_EMAIL
elif [ "$#" -eq 0 ]; then
    # Interactive mode - prompt for everything
    echo "Driver name (use underscores, e.g., my_usb_device):"
    read DRIVER_NAME
    
    echo "Driver class name (PascalCase, e.g., MyUsbDevice):"
    read DRIVER_CLASS
    
    # Try to get author info from git config
    GIT_NAME=$(git config user.name 2>/dev/null || echo "")
    GIT_EMAIL=$(git config user.email 2>/dev/null || echo "")
    
    prompt_with_default "Author name" "$GIT_NAME" "AUTHOR_NAME"
    prompt_with_default "Author email" "$GIT_EMAIL" "AUTHOR_EMAIL"
    
    export DRIVER_NAME
    export DRIVER_CLASS
    export AUTHOR_NAME
    export AUTHOR_EMAIL
else
    echo "Usage:"
    echo "  create_driver.sh <driver_name> <driver_class> <author_name> <author_email>  # Full specification"
    echo "  create_driver.sh <driver_name> <driver_class>                               # Auto-detect author from git config"
    echo "  create_driver.sh                                                            # Interactive mode"
    echo ""
    echo "Examples:"
    echo "  create_driver.sh mydriver MyDriver \"John Something\" john@somewhere.com"
    echo "  create_driver.sh mydriver MyDriver  # Will use git config for author info"
    echo "  create_driver.sh                    # Interactive prompts"
    exit 1
fi

# Validate required parameters
if [ -z "$DRIVER_NAME" ] || [ -z "$DRIVER_CLASS" ] || [ -z "$AUTHOR_NAME" ] || [ -z "$AUTHOR_EMAIL" ]; then
    echo "Error: All parameters are required"
    echo "Driver name: '$DRIVER_NAME'"
    echo "Driver class: '$DRIVER_CLASS'"
    echo "Author name: '$AUTHOR_NAME'"
    echo "Author email: '$AUTHOR_EMAIL'"
    exit 1
fi


# Convert driver name to kebab case for directory name
DRIVER_NAME_KEBAB=$(echo ${DRIVER_NAME} | sed 's/_/-/g')
export DRIVER_NAME_KEBAB

# create the driver directory
DRIVER_DIRECTORY=packages/jumpstarter-driver-${DRIVER_NAME_KEBAB}
MODULE_DIRECTORY=${DRIVER_DIRECTORY}/jumpstarter_driver_${DRIVER_NAME}
# create the module directories
mkdir -p ${MODULE_DIRECTORY}
mkdir -p ${DRIVER_DIRECTORY}/examples

# Define paths
DOCS_DIRECTORY=docs/source/reference/package-apis/drivers
DOC_FILE=${DOCS_DIRECTORY}/${DRIVER_NAME_KEBAB}.md
README_FILE=${DRIVER_DIRECTORY}/README.md

# Create README.md file with initial documentation
echo "Creating README.md file: ${README_FILE}"
cat > "${README_FILE}" << 'EOF'
# ${DRIVER_CLASS} Driver

`jumpstarter-driver-${DRIVER_NAME_KEBAB}` provides functionality for interacting with ${DRIVER_NAME} devices.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-${DRIVER_NAME_KEBAB}
```

## Configuration

Example configuration:

```yaml
export:
  ${DRIVER_NAME}:
    type: jumpstarter_driver_${DRIVER_NAME}.driver.${DRIVER_CLASS}
    config:
      # Add required config parameters here
```

## API Reference

Add API documentation here.
EOF
# Need to expand variables after EOF to prevent early expansion
if command -v gsed &> /dev/null; then
    gsed -i "s/\${DRIVER_CLASS}/${DRIVER_CLASS}/g; s/\${DRIVER_NAME_KEBAB}/${DRIVER_NAME_KEBAB}/g; s/\${DRIVER_NAME}/${DRIVER_NAME}/g" "${README_FILE}"
elif [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s/\${DRIVER_CLASS}/${DRIVER_CLASS}/g; s/\${DRIVER_NAME_KEBAB}/${DRIVER_NAME_KEBAB}/g; s/\${DRIVER_NAME}/${DRIVER_NAME}/g" "${README_FILE}"
else
    sed -i "s/\${DRIVER_CLASS}/${DRIVER_CLASS}/g; s/\${DRIVER_NAME_KEBAB}/${DRIVER_NAME_KEBAB}/g; s/\${DRIVER_NAME}/${DRIVER_NAME}/g" "${README_FILE}"
fi
echo "README.md file content:"
cat "${README_FILE}"

# Create symlink from documentation directory to README.md
mkdir -p ${DOCS_DIRECTORY}
echo "Creating symlink to README.md file"
rel_path=$(python3 -c "import os.path; print(os.path.relpath('${README_FILE}', '${DOCS_DIRECTORY}'))")
ln -sf "${rel_path}" "${DOC_FILE}"
echo "Created symlink: ${DOC_FILE} -> ${rel_path}"

for f in __init__.py client.py driver_test.py driver.py; do
    echo "Creating: ${MODULE_DIRECTORY}/${f}"
    envsubst < __templates__/driver/jumpstarter_driver/${f}.tmpl > ${MODULE_DIRECTORY}/${f}
done

for f in .gitignore pyproject.toml examples/exporter.yaml; do
    echo "Creating: ${DRIVER_DIRECTORY}/${f}"
    envsubst < __templates__/driver/${f}.tmpl > ${DRIVER_DIRECTORY}/${f}
done

# Add the new driver to the workspace sources in the main pyproject.toml
echo "Adding driver to workspace sources in pyproject.toml"
PACKAGE_NAME="jumpstarter-driver-${DRIVER_NAME_KEBAB}"
PYPROJECT_FILE="pyproject.toml"

# Create a temporary file to store the modified pyproject.toml
TEMP_FILE=$(mktemp)

# Read the pyproject.toml and insert the new driver in alphabetical order
python3 << EOF
import re

# Read the pyproject.toml file
with open('${PYPROJECT_FILE}', 'r') as f:
    content = f.read()

# Find the [tool.uv.sources] section
sources_pattern = r'(\[tool\.uv\.sources\]\n)(.*?)(\n\[)'
match = re.search(sources_pattern, content, re.DOTALL)

if match:
    # Get the parts: before sources section, sources content, and after sources section
    before_sources = content[:match.start()]
    sources_header = match.group(1)
    sources_content = match.group(2)
    after_sources = content[match.end(2):]
    
    # Parse existing sources
    sources = []
    for line in sources_content.strip().split('\n'):
        if line.strip() and '=' in line:
            package_name = line.split('=')[0].strip()
            sources.append((package_name, line))
    
    # Add the new driver
    new_line = '${PACKAGE_NAME} = { workspace = true }'
    sources.append(('${PACKAGE_NAME}', new_line))
    
    # Sort by package name
    sources.sort(key=lambda x: x[0])
    
    # Reconstruct the sources section
    new_sources_content = '\n'.join([line for _, line in sources])
    
    # Reconstruct the entire file
    new_content = before_sources + sources_header + new_sources_content + after_sources
    
    with open('${TEMP_FILE}', 'w') as f:
        f.write(new_content)
else:
    print("Error: Could not find [tool.uv.sources] section in pyproject.toml")
    exit(1)
EOF

# Replace the original file with the modified version
mv "${TEMP_FILE}" "${PYPROJECT_FILE}"
echo "Added ${PACKAGE_NAME} to workspace sources"
