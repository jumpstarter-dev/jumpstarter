{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };
  outputs =
    { nixpkgs, ... }:
    {
      devShells.x86_64-linux.default =
        with nixpkgs.legacyPackages.x86_64-linux;
        mkShell {
          env = {
            TEST_ASSET_KUBE_APISERVER = lib.getExe' kubernetes "kube-apiserver";
            TEST_ASSET_ETCD = lib.getExe' etcd "etcd";
            TEST_ASSET_KUBECTL = lib.getExe' kubectl "kubectl";
          };
        };
    };
}
