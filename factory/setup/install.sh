#!/bin/sh
# Stand up the four seats of this factory in Band Desktop.
#
#   factory/setup/install.sh <workspace> <result-repo> [--create]
#
# <workspace>   a folder of your choice for seat configs and wrappers (outside the result repo)
# <result-repo> absolute path of the result repository the seats work in
# --create      also run `band agent create` for each seat (otherwise print the commands)
#
# Before running: Band Desktop is installed and signed in, Docker is running, Claude Code is
# installed, and a Claude subscription token is stored in the macOS keychain as
# "df-claude-oauth" (`claude setup-token`, then
# `security add-generic-password -U -a "$USER" -s df-claude-oauth -w`). On Linux, export
# CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY instead and edit the launcher below.
set -e
WORK="$1"; RESULT="$2"; CREATE="$3"
[ -n "$WORK" ] && [ -n "$RESULT" ] || { echo "usage: $0 <workspace> <result-repo> [--create]"; exit 2; }
FACTORY="$(cd "$(dirname "$0")/.." && pwd)"
CLAUDE_BIN="$(command -v claude)"
mkdir -p "$WORK/bin" "$WORK/config-open" "$WORK/config-restricted/skills"

# Two isolated Claude Code configs: no personal memory, plugins or MCP servers.
# "restricted" seats (coordinator, builder, designer) are denied the holdout folder.
sed "s#__HOLDOUT_GLOB__#$(dirname "$RESULT")/**/holdout/**#g" \
    "$FACTORY/seat-settings/restricted-seat.template.json" > "$WORK/config-restricted/settings.json"
cp "$FACTORY/seat-settings/open-seat.template.json" "$WORK/config-open/settings.json"
cp -R "$FACTORY/skills/product-ui-design" "$WORK/config-restricted/skills/"

cat > "$WORK/bin/claude-launch.sh" <<EOF
#!/bin/sh
# Isolated Claude Code: token from the keychain, factory tools (timeout) on PATH.
CLAUDE_CODE_OAUTH_TOKEN="\$(security find-generic-password -a "\$USER" -s df-claude-oauth -w 2>/dev/null)"
export CLAUDE_CODE_OAUTH_TOKEN
export PATH="$FACTORY/tools:\$PATH"
exec "$CLAUDE_BIN" "\$@"
EOF
chmod +x "$WORK/bin/claude-launch.sh"

seat() {  # seat <name> <model> <config: open|restricted>
    NAME=$1; MODEL=$2; CONFIG=$3
    cat > "$WORK/bin/claude-$NAME.sh" <<EOF
#!/bin/sh
# Seat "$NAME": its own git identity and Claude config.
export GIT_AUTHOR_NAME="$NAME" GIT_COMMITTER_NAME="$NAME"
export GIT_AUTHOR_EMAIL="$NAME@dark-factory.local" GIT_COMMITTER_EMAIL="$NAME@dark-factory.local"
export CLAUDE_CONFIG_DIR="$WORK/config-$CONFIG"
exec "$WORK/bin/claude-launch.sh" "\$@"
EOF
    chmod +x "$WORK/bin/claude-$NAME.sh"
    CMD="band agent create --name $NAME --cwd $RESULT --session df-$NAME --transport claude-code-cli \
--spawn-command $WORK/bin/claude-$NAME.sh --runtime-model $MODEL --claude-permission-mode bypassPermissions \
--claude-context-mode local_config --no-spawn-sandbox --instructions-file $RESULT/mandates/$NAME.md"
    if [ "$CREATE" = "--create" ]; then sh -c "$CMD"; else echo "$CMD"; fi
}

seat coordinator claude-sonnet-5 restricted
seat builder     claude-opus-5-5 restricted
seat tester      claude-opus-5-5 open
seat designer    claude-opus-5-5 restricted

echo
echo "Next: log in once per config is not needed (token comes from the keychain)."
echo "Create a room in Band Desktop, add the four seats, and dispatch a task built from"
echo "factory/TASK_TEMPLATE.md to @<owner>/coordinator."
