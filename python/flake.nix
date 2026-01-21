{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };
  outputs =
    { nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        deps =
          pkgs:
          with pkgs;
          [
            # core development utils
            uv
            ruff
            gnumake
            buf
            # network driver
            iperf3
            # qemu driver
            qemu
          ]
          ++ lib.optionals stdenv.hostPlatform.isLinux [
            # ustreamer driver
            ustreamer
          ];
      in
      {
        devShells = {
          default = pkgs.mkShell {
            name = "jumpstarter-dev";
            nativeBuildInputs = deps pkgs;
          };
        };
        packages = {
          dev-fhs = pkgs.buildFHSEnv {
            name = "jumpstarter-dev-fhs";
            targetPkgs =
              ps:
              deps ps
              ++ (with ps; [
                # various drivers requiring libusb/libz
                libusb1
                libz
              ]);
            profile = ''
              export UV_MANAGED_PYTHON=1
              export SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt
            '';
          };
        };
      }
    );
}
