# Tasks: Rename --insecure-tls-config to --insecure-tls

## T001 [US1] Write failing test that --insecure-tls is accepted
- [x] Add test in opt_test.py that creates a Click command using the new option and invokes it with `--insecure-tls`
- [x] Verify the flag value is passed correctly to the command function as `insecure_tls`

## T002 [US1] Write failing test that --insecure-tls-config emits deprecation warning
- [x] Add test in opt_test.py that invokes a command with `--insecure-tls-config`
- [x] Verify the command still succeeds (backward compatibility)
- [x] Verify a deprecation warning is printed to stderr

## T003 [US1] Rename the option in opt.py
- [x] Change primary flag from `--insecure-tls-config` to `--insecure-tls`
- [x] Change the Python parameter name from `insecure_tls_config` to `insecure_tls`
- [x] Add `--insecure-tls-config` as a hidden deprecated alias with a callback that emits a warning
- [x] Update `confirm_insecure_tls` function signature to use `insecure_tls` parameter name
- [x] Rename the exported symbol from `opt_insecure_tls_config` to `opt_insecure_tls`

## T004 [US1] Update all call sites
- [x] Update jumpstarter_cli/login.py: imports, decorator, parameter name, and usages
- [x] Update jumpstarter_cli_admin/create.py: imports, decorator, parameter name, and usages
- [x] Update jumpstarter_cli_admin/import_res.py: imports, decorator, parameter name, and usages

## T005 [US2] Update tests and documentation references
- [x] Update create_test.py: change `--insecure-tls-config` to `--insecure-tls` in test invocations
- [x] Update import_res_test.py: change `--insecure-tls-config` to `--insecure-tls` in test invocations
- [x] Update documentation files that reference `--insecure-tls-config`

## T006 Run tests and linting
- [x] Run unit tests for jumpstarter-cli-common
- [x] Run unit tests for jumpstarter-cli-admin
- [x] Run linting with make lint-fix
