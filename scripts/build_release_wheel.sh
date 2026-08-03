#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 OUTPUT_DIR" >&2
  exit 2
fi

repository_root=$(git rev-parse --show-toplevel)
output_dir=$1
release_python=${RELEASE_PYTHON:-python3}

"$release_python" - <<'PY'
from importlib.metadata import version

required = {
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}
actual = {distribution: version(distribution) for distribution in required}
if actual != required:
    detail = ", ".join(
        f"{distribution}=={actual[distribution]} (expected {required[distribution]})"
        for distribution in required
    )
    raise SystemExit(
        "release wheel requires the pinned build toolchain: " + detail
    )
PY

if [ -n "$(git -C "$repository_root" status --porcelain --untracked-files=all)" ]; then
  echo "release wheel requires a clean Git worktree" >&2
  exit 1
fi

release_sha=$(git -C "$repository_root" rev-parse --verify HEAD^{commit})
source_date_epoch=$(git -C "$repository_root" show -s --format=%ct "$release_sha")
release_stage=$(mktemp -d /tmp/agent-driver-release-wheel-XXXXXX)

cleanup() {
  case "$release_stage" in
    /tmp/agent-driver-release-wheel-*) rm -r -- "$release_stage" ;;
    *) echo "refusing unsafe cleanup path: $release_stage" >&2 ;;
  esac
}
trap cleanup EXIT

export LC_ALL=C
export TZ=UTC
export PYTHONHASHSEED=0
export SOURCE_DATE_EPOCH="$source_date_epoch"
umask 022

source_tree="$release_stage/source"
mkdir -p "$source_tree" "$output_dir"
git -C "$repository_root" archive --format=tar "$release_sha" | tar -xf - -C "$source_tree"

# Git records only executable/non-executable, while a checkout's group/other
# write bits depend on umask. Wheel ZIP metadata preserves those bits for
# script-files, so normalize every tracked regular file from the Git index.
find "$source_tree" -type f -exec chmod 0644 {} +
while IFS= read -r -d '' release_entry; do
  release_metadata=${release_entry%%$'\t'*}
  release_path=${release_entry#*$'\t'}
  release_mode=${release_metadata%% *}
  if [ "$release_mode" = "100755" ] && [ -f "$source_tree/$release_path" ] && [ ! -L "$source_tree/$release_path" ]; then
    chmod 0755 "$source_tree/$release_path"
  fi
done < <(git -C "$repository_root" ls-files --stage -z)
find "$source_tree" -type d -exec chmod 0755 {} +

"$release_python" -m pip wheel "$source_tree" \
  --no-deps \
  --no-build-isolation \
  --wheel-dir "$output_dir"

wheel_count=$(find "$output_dir" -maxdepth 1 -type f -name 'agent_driver-*.whl' | wc -l)
if [ "$wheel_count" -ne 1 ]; then
  echo "expected exactly one agent_driver wheel in $output_dir, found $wheel_count" >&2
  exit 1
fi

wheel_path=$(find "$output_dir" -maxdepth 1 -type f -name 'agent_driver-*.whl' -print)
echo "release_source_sha=$release_sha"
echo "source_date_epoch=$source_date_epoch"
sha256sum "$wheel_path"
