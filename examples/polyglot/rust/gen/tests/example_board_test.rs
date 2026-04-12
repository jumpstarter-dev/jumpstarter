// Auto-generated test example by `jmp codegen --language rust --test-fixtures`.
// Do not edit — regenerate when the ExporterClass changes.

use jumpstarter_testing::jumpstarter_test;

use crate::devices::example_board::ExampleBoardDevice;

/// Example test showing how to use the generated device wrapper.
///
/// The `#[jumpstarter_test]` macro automatically creates an `ExporterSession`
/// from the `JUMPSTARTER_HOST` environment variable and constructs the typed
/// device wrapper.
#[jumpstarter_test]
async fn test_example_board_smoke(device: ExampleBoardDevice<'_>) {
    // Access typed driver clients directly:
    // device.power.method_name(()).await.unwrap();
    // device.storage.method_name(()).await.unwrap();
    // if let Some(ref mut network) = device.network { /* ... */ }
}
