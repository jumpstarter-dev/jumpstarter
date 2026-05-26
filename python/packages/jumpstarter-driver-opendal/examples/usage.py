# Get all created resources (files and directories)
created_resources = await driver.get_created_resources()  # Returns set[str]

# Example usage
for path in created_resources:
    print(f"Created: {path}")
