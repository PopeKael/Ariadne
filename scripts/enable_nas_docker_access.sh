#!/bin/sh
# Explicitly authorized persistent Docker administration for the existing SSH user.
set -eu
[ "$(id -u)" = "0" ] || { echo "Run this script with NAS sudo." >&2; exit 1; }
rule=/etc/sudoers.d/ariadne-codex-docker
entry="Wazza ALL=(root) NOPASSWD: /usr/local/bin/docker"
[ -x /usr/local/bin/docker ] || { echo "Docker executable missing; nothing changed." >&2; exit 1; }
grep -Eq "^[[:space:]]*[@#]includedir[[:space:]]+/etc/sudoers.d([[:space:]]|$)" /etc/sudoers || {
    echo "sudoers.d is not included; nothing changed. Inspect NAS sudo configuration." >&2
    exit 1
}
created=no
temporary=
cleanup() {
    [ -z "$temporary" ] || rm -f "$temporary"
    if [ "$created" = "yes" ]; then rm -f "$rule"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
if [ -e "$rule" ]; then
    [ ! -L "$rule" ] && [ "$(cat "$rule")" = "$entry" ] || {
        echo "An unexpected rule already exists; refusing to overwrite it." >&2
        exit 1
    }
else
    temporary=$(mktemp)
    printf "%s\n" "$entry" > "$temporary"
    for validator in /usr/sbin/visudo /usr/bin/visudo /sbin/visudo; do
        if [ -x "$validator" ]; then "$validator" -cf "$temporary"; break; fi
    done
    /usr/bin/install -o root -g root -m 0440 "$temporary" "$rule"
    created=yes
fi
# Start as Wazza and ignore cached authentication: prove the actual NOPASSWD rule.
/usr/bin/sudo -u Wazza -n /usr/bin/sudo -k -n /usr/local/bin/docker version --format '{{.Server.Version}}'
created=no
printf "%s\n" "DOCKER_ACCESS_READY: Wazza can run sudo -n /usr/local/bin/docker across SSH sessions."
