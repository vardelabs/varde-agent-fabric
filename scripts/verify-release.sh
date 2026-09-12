#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s --repo PATH --commit SHA --artifact PATH --sha256 HEX --rollback-commit SHA\n' "$0" >&2
  exit 2
}

repo=
commit=
artifact=
expected_sha256=
rollback_commit=
while (($#)); do
  case "$1" in
    --repo) (($# >= 2)) || usage; repo=$2; shift 2 ;;
    --commit) (($# >= 2)) || usage; commit=$2; shift 2 ;;
    --artifact) (($# >= 2)) || usage; artifact=$2; shift 2 ;;
    --sha256) (($# >= 2)) || usage; expected_sha256=$2; shift 2 ;;
    --rollback-commit) (($# >= 2)) || usage; rollback_commit=$2; shift 2 ;;
    *) usage ;;
  esac
done

[[ -d "$repo/.git" || -f "$repo/.git" ]] || { printf 'release verification: repository is not a Git worktree\n' >&2; exit 1; }
[[ -n "$commit" && "$commit" =~ ^[0-9a-fA-F]{40}$ ]] || { printf 'release verification: commit must be a 40-character SHA\n' >&2; exit 1; }
[[ -n "$rollback_commit" && "$rollback_commit" =~ ^[0-9a-fA-F]{40}$ ]] || { printf 'release verification: rollback commit must be a 40-character SHA\n' >&2; exit 1; }
[[ "$commit" != "$rollback_commit" ]] || { printf 'release verification: rollback commit must differ from release commit\n' >&2; exit 1; }
[[ "$expected_sha256" =~ ^[0-9a-fA-F]{64}$ ]] || { printf 'release verification: sha256 must be a 64-character hexadecimal digest\n' >&2; exit 1; }
[[ -f "$artifact" && -r "$artifact" ]] || { printf 'release verification: artifact must be a readable regular file\n' >&2; exit 1; }

resolved=$(git -C "$repo" rev-parse --verify "$commit^{commit}" 2>/dev/null) || { printf 'release verification: release commit is absent\n' >&2; exit 1; }
rollback_resolved=$(git -C "$repo" rev-parse --verify "$rollback_commit^{commit}" 2>/dev/null) || { printf 'release verification: rollback commit is absent\n' >&2; exit 1; }
[[ "$resolved" == "${commit,,}" ]] || { printf 'release verification: commit does not resolve exactly\n' >&2; exit 1; }
[[ "$rollback_resolved" == "${rollback_commit,,}" ]] || { printf 'release verification: rollback commit does not resolve exactly\n' >&2; exit 1; }
git -C "$repo" merge-base --is-ancestor "$rollback_resolved" "$resolved" || { printf 'release verification: rollback commit is not an ancestor of release commit\n' >&2; exit 1; }

actual_sha256=$(sha256sum "$artifact" | cut -d' ' -f1)
[[ "$actual_sha256" == "${expected_sha256,,}" ]] || { printf 'release verification: artifact checksum mismatch\n' >&2; exit 1; }

printf '{"artifact_sha256":"%s","rollback_commit":"%s","tested_commit":"%s"}\n' "$actual_sha256" "$resolved" "$resolved"
