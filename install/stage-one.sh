#!/usr/bin/env bash
# Stage one of the box install: the one root line on a new box.
#
#   sudo bash stage-one.sh --anchor <deployment commit>
#
# It asks for the box's 1Password service account token and seals it with
# systemd-creds. It reads everything else from the one item named
# box-bootstrap in the only vault that token can see, so this file holds no
# host, account or secret; its only fixed names are the box deployer's paths. Then it installs base packages,
# joins the tailnet, and installs the box deployer (stage two) from the
# deployment repo at the anchor commit. Safe to re-run.
set -euo pipefail

ROOT=${STAGE_ONE_ROOT:-}
CREDSTORE=$ROOT/etc/credstore.encrypted
TOKEN_CRED=$CREDSTORE/varde-box.op-token
BOX_DIR=$ROOT/etc/varde-box
DEPLOY_ETC=$ROOT/etc/varde-agent-deploy
STATE=$ROOT/var/lib/varde-box-deploy
LIBEXEC=$ROOT/usr/local/libexec/varde-box-deployer
UNIT_DIR=$ROOT/etc/systemd/system
APT_KEYRINGS=$ROOT/usr/share/keyrings
APT_SOURCES=$ROOT/etc/apt/sources.list.d
ITEM=box-bootstrap
DEPLOYER_FILES=(box_deployer.py dispatcher_deployer.py gateway_deployer.py)
UNITS=(varde-box-deploy.service varde-box-deploy.timer varde-box-deployer-rollback.service)
BASE_PACKAGES=(ca-certificates curl git gnupg jq python3 rasdaemon unattended-upgrades)

die() {
  echo "stage-one: $*" >&2
  exit 1
}

say() {
  echo "stage-one: $*"
}

parse_args() {
  ANCHOR=
  NEW_TOKEN=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --anchor) ANCHOR=${2:-}; shift 2 ;;
      --new-token) NEW_TOKEN=1; shift ;;
      *) die "unknown argument: $1" ;;
    esac
  done
  [[ $ANCHOR =~ ^[0-9a-f]{40}$ ]] || die "--anchor takes the full 40-character deployment commit"
}

require_box() {
  [ "$(id -u)" = 0 ] || die "run as root: sudo bash $0 --anchor <commit>"
  grep -qx 'ID=ubuntu' "$ROOT/etc/os-release" || die "this script supports Ubuntu only"
  command -v systemd-creds >/dev/null || die "systemd-creds is missing; it needs systemd 250 or later"
}

# Both vendors sign their apt repositories; the keys come over HTTPS from the
# vendor's own host, so apt verifies every later package against them.
add_vendor_repos() {
  local codename arch
  codename=$(. "$ROOT/etc/os-release" && echo "$VERSION_CODENAME")
  arch=$(dpkg --print-architecture)
  install -d -m 0755 "$APT_KEYRINGS" "$APT_SOURCES"
  [ -s "$APT_KEYRINGS/1password-archive-keyring.gpg" ] ||
    curl -fsSL https://downloads.1password.com/linux/keys/1password.asc | gpg --dearmor --yes -o "$APT_KEYRINGS/1password-archive-keyring.gpg"
  echo "deb [arch=$arch signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$arch stable main" \
    >"$APT_SOURCES/1password.list"
  [ -s "$APT_KEYRINGS/tailscale-archive-keyring.gpg" ] ||
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/$codename.noarmor.gpg" -o "$APT_KEYRINGS/tailscale-archive-keyring.gpg"
  # apt reads a keyring as _apt, and a strict umask under sudo would leave these unreadable to it.
  chmod 0644 "$APT_KEYRINGS/1password-archive-keyring.gpg" "$APT_KEYRINGS/tailscale-archive-keyring.gpg"
  echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu $codename main" \
    >"$APT_SOURCES/tailscale.list"
}

install_packages() {
  say "installing base packages"
  apt-get update -q >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${BASE_PACKAGES[@]}" >/dev/null
  add_vendor_repos
  apt-get update -q >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q 1password-cli tailscale >/dev/null
  systemctl enable --now unattended-upgrades.service rasdaemon.service >/dev/null 2>&1 || true
}

# The token never reaches an argument list or a file in the clear: it is read
# with no echo and handed to systemd-creds and op on stdin or in the
# environment of one child.
seal_token() {
  local token
  if [ -s "$TOKEN_CRED" ] && [ "$NEW_TOKEN" = 0 ]; then
    say "using the sealed 1Password token; pass --new-token to replace it"
    return
  fi
  read -rsp "1Password service account token for this box: " token
  echo
  [ -n "$token" ] || die "no token entered"
  OP_SERVICE_ACCOUNT_TOKEN=$token op whoami >/dev/null 2>&1 || die "1Password refused that token; nothing was sealed"
  install -d -m 0700 "$CREDSTORE"
  printf '%s' "$token" | systemd-creds encrypt --name=varde-box.op-token - "$TOKEN_CRED.new"
  chmod 0600 "$TOKEN_CRED.new"
  mv "$TOKEN_CRED.new" "$TOKEN_CRED"
  say "sealed the token at $TOKEN_CRED"
}

with_token() {
  OP_SERVICE_ACCOUNT_TOKEN=$(systemd-creds decrypt --name=varde-box.op-token "$TOKEN_CRED" -) "$@"
}

# The box's service account sees one vault, so the item needs no vault name.
read_field() {
  local vaults
  vaults=$(with_token op vault list --format json | jq -r 'map(.id) | .[]')
  [ "$(printf '%s\n' "$vaults" | grep -c .)" = 1 ] || die "the token must see exactly one vault"
  with_token op read "op://$vaults/$ITEM/$1"
}

# tailscale status exits 0 whenever the daemon answers, logged in or not.
tailnet_joined() {
  [ "$(tailscale status --json 2>/dev/null | jq -r '.BackendState // empty')" = Running ]
}

join_tailnet() {
  if tailnet_joined; then
    say "already on the tailnet"
    return
  fi
  systemctl enable --now tailscaled.service >/dev/null
  KEY_FILE=$(mktemp)
  read_field tailscale-authkey >"$KEY_FILE"
  tailscale up --auth-key="file:$KEY_FILE" || die "tailscale up failed"
  rm -f "$KEY_FILE"
  say "joined the tailnet"
}

# A join key or a clone must not outlive a failed or interrupted run.
cleanup() {
  rm -f "${KEY_FILE:-}"
  rm -rf "${BOOT:-}" "${STAGE:-}"
}

# The deployer reads each of these files, so each one appears whole, and only
# when the command that writes it succeeds.
place() {
  local mode=$1 path=$2
  shift 2
  "$@" >"$path.new"
  chmod "$mode" "$path.new"
  mv "$path.new" "$path"
}

write_secret_file() {
  local field=$1 path=$2
  install -d -m 0755 "$(dirname "$path")"
  # A file left by a killed run keeps its old mode through a redirect.
  rm -f "$path.new"
  (umask 077 && read_field "$field" >"$path.new")
  mv "$path.new" "$path"
}

write_box_file() {
  local box
  box=$(read_field box)
  [[ $box =~ ^[a-z][a-z0-9-]*$ ]] || die "the vault item's box field is not a box name"
  install -d -m 0755 "$BOX_DIR"
  place 0644 "$BOX_DIR/box.json" printf '{"schema": "varde-box.v1", "box": "%s", "anchor_commit": "%s"}\n' "$box" "$ANCHOR"
}

# The anchor is what this root line vouches for. The deployer walks main's
# first-parent history from it, so a commit merged in from a side branch fails.
# The clone uses a private copy of the credential, so the deployer's files
# change only after the anchor checks out.
fetch_anchor() {
  local repo
  repo=$(read_field deployment-repo)
  BOOT=$(mktemp -d)
  (umask 077 && read_field git-credentials >"$BOOT/git-credentials")
  git -c credential.helper="store --file=$BOOT/git-credentials" clone -q --no-checkout "$repo" "$BOOT/deployment"
  git -C "$BOOT/deployment" cat-file -e "$ANCHOR^{commit}" 2>/dev/null || die "the anchor commit is not in the deployment repo"
  git -C "$BOOT/deployment" rev-list --first-parent origin/main | grep -x "$ANCHOR" >/dev/null ||
    die "the anchor commit is not on the deployment repo's main (first-parent history)"
}

anchor_file() {
  git -C "$BOOT/deployment" show "$ANCHOR:deploy/$1"
}

# The same hash box_deployer.py computes over its three files, so the
# deployer recognizes this release as itself.
release_digest() {
  python3 -I -c '
import hashlib, pathlib, sys
digest = hashlib.sha256()
for name in sys.argv[2:]:
    data = (pathlib.Path(sys.argv[1]) / name).read_bytes()
    digest.update(f"{name}\0{len(data)}\0".encode())
    digest.update(data)
print(digest.hexdigest())
' "$1" "${DEPLOYER_FILES[@]}"
}

install_deployer() {
  local digest name
  STAGE=$(mktemp -d)
  for name in "${DEPLOYER_FILES[@]}"; do
    anchor_file "$name" >"$STAGE/$name"
  done
  digest=$(release_digest "$STAGE")
  install -d -m 0755 "$LIBEXEC/releases" "$STATE"
  if [ ! -d "$LIBEXEC/releases/$digest" ]; then
    install -d -m 0755 "$LIBEXEC/releases/$digest.tmp"
    install -m 0644 "${DEPLOYER_FILES[@]/#/$STAGE/}" "$LIBEXEC/releases/$digest.tmp/"
    mv "$LIBEXEC/releases/$digest.tmp" "$LIBEXEC/releases/$digest"
  fi
  ln -sfn "$LIBEXEC/releases/$digest" "$LIBEXEC/current"
  place 0644 "$LIBEXEC/box_deployer_rollback.py" anchor_file box_deployer_rollback.py
  for name in "${UNITS[@]}"; do
    place 0644 "$UNIT_DIR/$name" anchor_file "$name"
  done
  say "installed the box deployer release $digest"
}

# The deployer's own lock, so a timer run never sees a half-written install,
# and two runs of this script never write the same file at once.
take_deploy_lock() {
  install -d -m 0755 "$STATE"
  exec 9>>"$STATE/deploy.lock"
  chmod 0600 "$STATE/deploy.lock"
  flock 9
}

start_deployer() {
  systemctl daemon-reload
  systemctl enable --now varde-box-deploy.timer >/dev/null
  say "the box deployer timer is on; its first run installs this box's merged settings"
}

main() {
  trap cleanup EXIT
  parse_args "$@"
  require_box
  install_packages
  take_deploy_lock
  seal_token
  join_tailnet
  fetch_anchor
  write_secret_file git-credentials "$DEPLOY_ETC/git-credentials"
  write_secret_file merge-approvers "$DEPLOY_ETC/merge-approvers.json"
  write_box_file
  install_deployer
  exec 9>&-
  start_deployer
}

main "$@"
