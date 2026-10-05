#!/usr/bin/env bash
# Seekter installer for macOS and Linux (Debian/Ubuntu).
#
# One line, pasted into Terminal:
#   curl -fsSL https://raw.githubusercontent.com/selfishprimate/seekter/main/scripts/install.sh | bash
#
# Safe to run again: everything already installed is checked, updated where it can be,
# and left alone otherwise. `--check` only reports what is missing and changes nothing.
#
# What it sets up, in order:
#   1. Apple's command line tools (git, python3, curl) / the same packages on Linux
#   2. Python 3.9 or newer
#   3. Google Chrome
#   4. Claude Code (installed, or updated if already there)
#   5. Sign-in to Claude (claude.ai account on a paid plan; the browser opens)
#   6. Seekter itself, in ~/seekter (or $SEEKTER_DIR)
#   7. The Claude in Chrome extension (opens the Chrome Web Store page)
#   8. Optional: a "Seekter" icon on the Desktop (macOS), then starts /seekter-init

set -u

REPO_URL="https://github.com/selfishprimate/seekter.git"
SEEKTER_DIR="${SEEKTER_DIR:-$HOME/seekter}"
EXT_ID="fcoeoabgfenejglbffodgkkbkcdhcgfn"
EXT_URL="https://chromewebstore.google.com/detail/claude/$EXT_ID"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# ---------- output ----------
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=; G=; Y=; R=; N=; fi
step() { printf '\n%s== %s ==%s\n' "$B" "$1" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
fail() { printf '\n%s✗ %s%s\n' "$R" "$1" "$N"; exit 1; }
MISSING=()
need() { MISSING+=("$1"); warn "missing: $1"; }

# Questions read from the keyboard even when the script arrives through `curl | bash`.
ask() {  # ask "Question" default(y|n) -> returns 0 for yes
    local a d="$2" hint="[Y/n]"; [ "$d" = n ] && hint="[y/N]"
    [ -r /dev/tty ] || { [ "$d" = y ]; return; }
    printf '  %s %s ' "$1" "$hint"; read -r a </dev/tty || a=""
    a="${a:-$d}"; case "$a" in [Yy]*) return 0 ;; *) return 1 ;; esac
}
pause() { [ -r /dev/tty ] && { printf '  %s ' "${1:-Press Enter to continue.}"; read -r _ </dev/tty; }; }
open_url() { if [ "$OS" = mac ]; then open -a "Google Chrome" "$1" 2>/dev/null || open "$1"; else xdg-open "$1" >/dev/null 2>&1 || echo "  Open this link: $1"; fi; }

# ---------- 0. which computer ----------
case "$(uname -s)" in
    Darwin) OS=mac ;;
    Linux)  OS=linux; grep -qi microsoft /proc/version 2>/dev/null && fail "This is Windows (WSL). Claude in Chrome does not work from WSL; see the README for Windows." ;;
    *)      fail "This installer supports macOS and Linux. On Windows, see the README." ;;
esac
printf '%sSeekter setup%s (%s)%s\n' "$B" "$N" "$OS" "$( [ $CHECK_ONLY = 1 ] && echo ', check only: nothing will be changed')"
echo "This takes 5 to 15 minutes. You may be asked for your computer password, to sign in to Claude, and to click 'Add to Chrome'."

# ---------- 1. basic tools ----------
step "1/8 Basic tools (git, python3, curl)"
if [ "$OS" = mac ]; then
    if xcode-select -p >/dev/null 2>&1; then
        ok "Apple command line tools are installed"
    elif [ $CHECK_ONLY = 1 ]; then
        need "Apple command line tools"
    else
        echo "  A window will open asking to install the command line tools. Click 'Install' and agree."
        xcode-select --install >/dev/null 2>&1
        printf '  Waiting for the install to finish'
        for _ in $(seq 1 180); do xcode-select -p >/dev/null 2>&1 && break; printf '.'; sleep 10; done
        echo
        xcode-select -p >/dev/null 2>&1 || fail "The command line tools did not finish installing. Run this script again once they have."
        ok "Apple command line tools installed"
    fi
else
    pkgs=()
    for c in git python3 curl; do command -v "$c" >/dev/null 2>&1 || pkgs+=("$c"); done
    if [ ${#pkgs[@]} -eq 0 ]; then
        ok "git, python3 and curl are installed"
    elif [ $CHECK_ONLY = 1 ]; then
        need "${pkgs[*]}"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "  Installing ${pkgs[*]} (your password may be asked)..."
        sudo apt-get update -qq && sudo apt-get install -y -qq "${pkgs[@]}" || fail "Could not install ${pkgs[*]}."
        ok "installed ${pkgs[*]}"
    else
        fail "Please install ${pkgs[*]} with your system's package manager, then run this again."
    fi
fi

# ---------- 2. python version ----------
step "2/8 Python 3.9 or newer"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    ok "$(python3 --version 2>&1)"
elif [ $CHECK_ONLY = 1 ]; then
    need "Python 3.9+"
else
    warn "Python is missing or older than 3.9. Opening the download page: install it, then run this script again."
    open_url "https://www.python.org/downloads/"
    exit 1
fi

# ---------- 3. Chrome ----------
step "3/8 Google Chrome"
chrome_ok() {
    if [ "$OS" = mac ]; then [ -d "/Applications/Google Chrome.app" ] || [ -d "$HOME/Applications/Google Chrome.app" ]
    else command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; fi
}
if chrome_ok; then
    ok "Chrome is installed (it updates itself)"
elif [ $CHECK_ONLY = 1 ]; then
    need "Google Chrome"
elif [ "$OS" = mac ]; then
    echo "  Downloading Chrome..."
    tmp="$(mktemp -d)"
    if curl -fsSL -o "$tmp/chrome.dmg" "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg" \
       && mnt="$(hdiutil attach -nobrowse -quiet "$tmp/chrome.dmg" | awk -F'\t' '/\/Volumes\//{print $NF; exit}')" \
       && cp -R "$mnt/Google Chrome.app" /Applications/ ; then
        hdiutil detach -quiet "$mnt" >/dev/null 2>&1
        ok "Chrome installed"
    else
        [ -n "${mnt:-}" ] && hdiutil detach -quiet "$mnt" >/dev/null 2>&1
        warn "Automatic install failed. Opening the download page: install Chrome, then run this again."
        open "https://www.google.com/chrome/"; exit 1
    fi
    rm -rf "$tmp"
elif command -v apt-get >/dev/null 2>&1 && [ "$(uname -m)" = x86_64 ]; then
    tmp="$(mktemp -d)"
    curl -fsSL -o "$tmp/chrome.deb" "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb" \
        && sudo apt-get install -y -qq "$tmp/chrome.deb" && ok "Chrome installed" || fail "Could not install Chrome. Get it from https://www.google.com/chrome/"
    rm -rf "$tmp"
else
    fail "Please install Google Chrome from https://www.google.com/chrome/, then run this again."
fi

# ---------- 4. Claude Code ----------
step "4/8 Claude Code"
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null 2>&1; then
    if [ $CHECK_ONLY = 1 ]; then ok "$(claude --version 2>/dev/null)"
    else claude update >/dev/null 2>&1; ok "$(claude --version 2>/dev/null) (checked for updates)"; fi
elif [ $CHECK_ONLY = 1 ]; then
    need "Claude Code"
else
    echo "  Installing Claude Code (official installer from claude.ai)..."
    curl -fsSL https://claude.ai/install.sh | bash || fail "The Claude Code installer failed."
    command -v claude >/dev/null 2>&1 || fail "Claude Code installed but cannot be found. Close Terminal, open it again, and re-run this."
    ok "$(claude --version 2>/dev/null)"
fi
# Make `claude` work in new Terminal windows too.
if [ $CHECK_ONLY = 0 ] && [ -x "$HOME/.local/bin/claude" ]; then
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
        { [ -f "$rc" ] || [ "$rc" = "$HOME/.zshrc" -a "$OS" = mac ]; } || continue
        grep -qs '.local/bin' "$rc" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc"
    done
fi

# ---------- 5. sign in ----------
step "5/8 Sign in to Claude"
auth_field() { claude auth status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('$1',''))" 2>/dev/null; }
if ! command -v claude >/dev/null 2>&1; then
    need "Claude sign-in (after Claude Code is installed)"
elif [ "$(auth_field loggedIn)" = True ] && [ "$(auth_field authMethod)" = claude.ai ]; then
    ok "signed in ($(auth_field subscriptionType) plan)"
elif [ $CHECK_ONLY = 1 ]; then
    need "Claude sign-in"
else
    echo "  Your browser will open. Sign in with your Claude account (Pro, Max, Team or Enterprise;"
    echo "  the free plan does not include Claude Code). Come back here when it says you're signed in."
    claude auth login </dev/tty || fail "Sign-in did not complete. Run this script again to retry."
    [ "$(auth_field loggedIn)" = True ] || fail "Still not signed in. Run this script again to retry."
    ok "signed in ($(auth_field subscriptionType) plan)"
fi
case "$(command -v claude >/dev/null 2>&1 && auth_field subscriptionType)" in
    free) warn "This account is on the free plan. Claude Code needs Pro or higher: https://claude.ai/upgrade" ;;
esac

# ---------- 6. Seekter ----------
step "6/8 Seekter"
if [ -d "$SEEKTER_DIR/.git" ]; then
    if [ $CHECK_ONLY = 1 ]; then ok "found in $SEEKTER_DIR"
    else
        git -C "$SEEKTER_DIR" pull --ff-only -q && ok "updated in $SEEKTER_DIR" \
            || warn "could not update $SEEKTER_DIR (local changes?); keeping it as it is"
    fi
elif [ -e "$SEEKTER_DIR" ]; then
    fail "$SEEKTER_DIR exists but is not Seekter. Move it, or run with SEEKTER_DIR=/another/folder."
elif [ $CHECK_ONLY = 1 ]; then
    need "Seekter (would go to $SEEKTER_DIR)"
else
    git clone -q "$REPO_URL" "$SEEKTER_DIR" || fail "Could not download Seekter."
    ok "downloaded to $SEEKTER_DIR"
fi
if [ -f "$SEEKTER_DIR/tests/test_seekter.py" ] && [ $CHECK_ONLY = 0 ]; then
    (cd "$SEEKTER_DIR" && python3 -m unittest discover -q tests >/dev/null 2>&1) \
        && ok "self-test passed" || warn "self-test failed; Seekter may still work. Mention it if something breaks."
fi

# ---------- 7. Claude in Chrome ----------
step "7/8 Claude in Chrome extension"
ext_ok() {
    local base
    if [ "$OS" = mac ]; then base="$HOME/Library/Application Support/Google/Chrome"; else base="$HOME/.config/google-chrome"; fi
    ls -d "$base"/*/Extensions/"$EXT_ID" >/dev/null 2>&1
}
if ext_ok; then
    ok "installed"
elif [ $CHECK_ONLY = 1 ]; then
    need "Claude in Chrome extension"
else
    echo "  Chrome will open on the extension page. Click 'Add to Chrome', then 'Add extension',"
    echo "  and sign in to Claude in the extension if it asks."
    open_url "$EXT_URL"
    pause "Press Enter here once the extension is added."
    ext_ok && ok "installed" || warn "Not found yet. If you added it, that's fine: Claude will check again when it starts."
fi
echo "  Also sign in to your email (Gmail, Outlook...) in this same Chrome: Seekter reads job-alert emails there."

# ---------- 8. finish ----------
step "8/8 Finish"
if [ $CHECK_ONLY = 1 ]; then
    if [ ${#MISSING[@]} -eq 0 ]; then ok "everything is ready"; else
        echo "  Missing: ${MISSING[*]}"; echo "  Run without --check to install them."; fi
    exit 0
fi
LAUNCH="cd \"$SEEKTER_DIR\" && claude --chrome"
if [ "$OS" = mac ] && [ -d "$HOME/Desktop" ] && [ ! -e "$HOME/Desktop/Seekter.command" ]; then
    if ask "Put a 'Seekter' icon on your Desktop? Double-click it to open Seekter." y; then
        printf '#!/bin/bash\nexport PATH="$HOME/.local/bin:$PATH"\n%s\n' "$LAUNCH" > "$HOME/Desktop/Seekter.command"
        chmod +x "$HOME/Desktop/Seekter.command"
        ok "Desktop icon created (the first time, macOS may ask you to confirm opening it)"
    fi
fi
echo
echo "${B}All set.${N} To open Seekter later: $( [ -e "$HOME/Desktop/Seekter.command" ] && echo 'double-click Seekter on your Desktop, or ')open Terminal and type"
echo "    $LAUNCH"
echo "The first time, type ${B}/seekter-init${N}: it asks about 40 short questions about you and your job search."
if ask "Start Seekter now and begin with /seekter-init?" y; then
    cd "$SEEKTER_DIR" && exec claude --chrome "/seekter-init" </dev/tty
fi
