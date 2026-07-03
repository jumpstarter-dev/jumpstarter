fn main() {
    // The whole build: resolve the committed exporter.yaml against interfaces/registry +
    // interfaces/proto (proto-only, strict) and generate the typed device wrapper into OUT_DIR.
    jumpstarter_codegen::build::exporter_device("exporter.yaml");
}
