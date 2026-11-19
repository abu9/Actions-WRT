#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/reusable_build.yml"
PASS=0
TEST_ROOT=

fail(){ echo "FAIL: $*" >&2; exit 1; }
assert_has(){ grep -Fq -- "$2" <<<"$1" || fail "missing '$2' in extracted workflow step"; }
assert_lacks(){ ! grep -Fq -- "$2" <<<"$1" || fail "unexpected '$2' in extracted workflow step"; }

extract_step(){
  awk -v wanted="$1" '
    $0 == "      - name: " wanted { found=1; next }
    found && /^      - name: / { exit }
    found && /^        run: [|]$/ { in_run=1; next }
    in_run && /^          / { sub(/^          /,""); print; next }
    in_run && /^[[:space:]]*$/ { print ""; next }
    in_run { exit }
  ' "$WORKFLOW"
}

test_clean_shell_invocation(){
  local step_name="$1" script_name="$2" run_script workspace fixture
  TEST_ROOT="$(mktemp -d)"
  workspace="$TEST_ROOT"
  mkdir -p "$workspace/openwrt"
  fixture="$workspace/$script_name"
  cat > "$fixture" <<'FIXTURE'
#!/usr/bin/env bash
set -euo pipefail
: "\${GITHUB_WORKSPACE:?GITHUB_WORKSPACE was not resolved}"
printf '%s\n' "$GITHUB_WORKSPACE" > "$GITHUB_WORKSPACE/observed-workspace"
FIXTURE

  run_script="$(extract_step "$step_name")"
  [[ -n "$run_script" ]] || fail "could not extract run block for $step_name"
  (
    cd "$workspace/openwrt"
    env -i PATH="$PATH" bash --noprofile --norc -eo pipefail -c "$run_script"
  )
  [[ "$(cat "$workspace/observed-workspace")" == "$workspace" ]] || fail "$script_name did not receive the resolved workspace"
  rm -rf "$TEST_ROOT"
  TEST_ROOT=
}

test_part1_workspace_resolution(){
  local script
  script="$(extract_step 'Load pre-custom script')"
  assert_has "$script" 'GITHUB_WORKSPACE="$(cd .. && pwd)"'
  assert_has "$script" 'export GITHUB_WORKSPACE'
  assert_has "$script" 'bash "$GITHUB_WORKSPACE/diy-part1.sh"'
  assert_lacks "$script" 'bash "\${GITHUB_WORKSPACE}/diy-part1.sh"'
}

test_part1_clean_shell(){
  test_clean_shell_invocation 'Load pre-custom script' diy-part1.sh
}

test_part2_workspace_resolution(){
  local script
  script="$(extract_step 'Load post-custom script')"
  assert_has "$script" 'GITHUB_WORKSPACE="$(cd .. && pwd)"'
  assert_has "$script" 'export GITHUB_WORKSPACE'
  assert_has "$script" 'bash "$GITHUB_WORKSPACE/diy-part2.sh"'
}

test_part2_clean_shell(){
  test_clean_shell_invocation 'Load post-custom script' diy-part2.sh
}

run_test(){
  "$2"
  PASS=$((PASS+1))
  printf 'ok %02d - %s\n' "$PASS" "$1"
}

run_test 'pre-custom script resolves workspace inside buildbot shell' test_part1_workspace_resolution
run_test 'pre-custom script works without inherited runner environment' test_part1_clean_shell
run_test 'post-custom script resolves workspace inside buildbot shell' test_part2_workspace_resolution
run_test 'post-custom script works without inherited runner environment' test_part2_clean_shell
printf '\n%d DIY script invocation regression tests passed.\n' "$PASS"
