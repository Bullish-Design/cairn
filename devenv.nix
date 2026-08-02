{ pkgs, lib, config, inputs, ... }:

let
  # Sandbox runtime for the bwrap executor (cairn.runtime.sandbox).
  #
  # NixOS-only deployment: the sandbox interpreter, the bubblewrap binary, and
  # the interpreter's store closure are all declared here. The executor reads
  # CAIRN_EXECUTOR_* environment variables and binds exactly the declared
  # closure read-only — no runtime discovery of python dependencies.
  #
  # The sandbox python is the same interpreter as the dev environment
  # (languages.python.version), stdlib only — nixpkgs python ships no packages.
  sandboxPython = config.languages.python.package;
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
  # Point tools that honor VIRTUAL_ENV (ty, pip, pytest) at the uv-managed
  # devenv venv. devenv's uv integration pins the venv location via
  # UV_PROJECT_ENVIRONMENT, which ty does not honor.
  env.VIRTUAL_ENV = "${config.env.DEVENV_STATE}/venv";

  # https://devenv.sh/languages/
  # languages.rust.enable = true;
  languages = {
    python = {
      enable = true;
      version = "3.13";
      # Delegate venv management entirely to uv: uv syncs its venv (located at
      # $UV_PROJECT_ENVIRONMENT under .devenv/state) on shell entry with the
      # dev extras. devenv's own plain venv (venv.enable) would create a
      # second, dependency-free venv and shadow it via VIRTUAL_ENV.
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
    # devenv's uv integration manages its own venv (UV_PROJECT_ENVIRONMENT,
    # i.e. .devenv/test-state/venv) and syncs it via the devenv:python:uv task;
    # the test runner does not put it on PATH.
    test -n "$UV_PROJECT_ENVIRONMENT" || { echo "UV_PROJECT_ENVIRONMENT not set"; exit 1; }
    test -x "$UV_PROJECT_ENVIRONMENT/bin/pytest" || { echo "devenv venv not synced (pytest missing)"; exit 1; }
    export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
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
