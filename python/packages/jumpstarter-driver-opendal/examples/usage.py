created_resources = driver.get_created_resources()

for path in created_resources:
    print(f"Created: {path}")
