{ pkgs, lib, config, inputs, ... }:

let
  # Sandbox runtime for the bwrap executor (cairn.runtime.sandbox).
  #
  # NixOS-only deployment: the sandbox interpreter, the bubblewrap binary, and
  # the interpreter's store closure are all declared here. The executor reads
  # CAIRN_EXECUTOR_* environment variables and binds exactly the declared
  # closure read-only — no runtime discovery of python dependencies.
  sandboxPython = pkgs.python3; # stdlib only (nixpkgs python ships no packages)
  sandboxClosureManifest = pkgs.writeClosure [ sandboxPython ];
in
{
  # https://devenv.sh/basics/
  env.GREET = "devenv";

  packages = [
    pkgs.git
    pkgs.bubblewrap
  ];

  env.CAIRN_EXECUTOR_BWRAP_PATH = "${pkgs.bubblewrap}/bin/bwrap";
  env.CAIRN_EXECUTOR_PYTHON_PATH = "${sandboxPython}/bin/python3";
  # Realized as part of the shell derivation via the env value.
  env.CAIRN_EXECUTOR_SANDBOX_CLOSURE_PATH = sandboxClosureManifest;

  # https://devenv.sh/languages/
  # languages.rust.enable = true;
  languages = {
    python = {
      enable = true;
      version = "3.13";
      # Delegate venv management entirely to uv: uv owns .venv and syncs it on
      # shell entry. devenv's own venv (venv.enable) would create a second,
      # dependency-free venv and shadow it via VIRTUAL_ENV.
      uv = {
        enable = true;
        sync = {
          enable = true;
          extras = [ "dev" ];
        };
      };
    };
  };

  # https://devenv.sh/processes/
  # processes.cargo-watch.exec = "cargo-watch";

  # https://devenv.sh/services/
  # services.postgres.enable = true;

  # https://devenv.sh/scripts/
  scripts.hello.exec = ''
    echo hello from $GREET
  '';

  enterShell = ''
    hello
    git --version
  '';

  # https://devenv.sh/tasks/
  # tasks = {
  #   "myproj:setup".exec = "mytool build";
  #   "devenv:enterShell".after = [ "myproj:setup" ];
  # };

  # https://devenv.sh/tests/
  enterTest = ''
    cd "$DEVENV_ROOT"
    # The test runner does not put uv's .venv/bin on PATH.
    export PATH="$DEVENV_ROOT/.venv/bin:$PATH"
    # Fail loudly if the sandbox runtime is not declared, instead of letting
    # the real-sandbox integration tests silently skip.
    test -n "$CAIRN_EXECUTOR_BWRAP_PATH" || { echo "CAIRN_EXECUTOR_BWRAP_PATH not set"; exit 1; }
    test -n "$CAIRN_EXECUTOR_PYTHON_PATH" || { echo "CAIRN_EXECUTOR_PYTHON_PATH not set"; exit 1; }
    echo "==> Type checking (ty)"
    ty check
    echo "==> Tests (pytest, incl. real-sandbox via CAIRN_EXECUTOR_*)"
    pytest -q
  '';

  # https://devenv.sh/pre-commit-hooks/
  # pre-commit.hooks.shellcheck.enable = true;

  # See full reference at https://devenv.sh/reference/options/
}
