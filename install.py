#!/usr/bin/env python3
"""
claude-code-rm-guard installer

This script installs the rm-guard hook for Claude Code, which prevents
destructive file operations (rm, unlink, rmdir, shred) outside your
working directory.

Usage:
    Install:   curl -fsSL https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/install.py | python3
    Uninstall: curl -fsSL https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/install.py | python3 - --uninstall

What this script does:
    1. Downloads validate-rm.py hook to ~/.claude/hooks/
    2. Adds PreToolUse hook configuration to ~/.claude/settings.json
    3. Optionally adds ask permissions for rm/unlink/rmdir commands

The script is non-destructive:
    - Never overwrites existing settings without merging
    - Preserves all existing hooks and permissions
    - Only removes entries it would have added during uninstall
"""

import json
import os
import stat
import sys
import urllib.request
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

# URL to download the hook script from
HOOK_URL = "https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/hooks/validate-rm.py"

# Where to install the hook
CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
HOOK_FILE = HOOKS_DIR / "validate-rm.py"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"

# The hook command that will be added to settings.json
HOOK_COMMAND = "python3 ~/.claude/hooks/validate-rm.py"

# The hook configuration object for PreToolUse
HOOK_CONFIG = {"type": "command", "command": HOOK_COMMAND}

# Permissions to optionally add (require user confirmation before rm commands)
ASK_PERMISSIONS = ["Bash(rm:*)", "Bash(unlink:*)", "Bash(rmdir:*)"]

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def print_header(text: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def print_status(symbol: str, message: str) -> None:
    """Print a status message with a symbol prefix."""
    print(f"  {symbol} {message}")


def print_success(message: str) -> None:
    print_status("✓", message)


def print_skip(message: str) -> None:
    print_status("•", message)


def print_error(message: str) -> None:
    print_status("✗", message)


def print_info(message: str) -> None:
    print_status("ℹ", message)


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """
    Ask user a yes/no question.

    When the script is piped (curl | python3), stdin is the pipe, not the terminal.
    We need to read from /dev/tty to get actual user input in that case.

    Args:
        prompt: The question to ask
        default: Default answer if user just presses Enter

    Returns:
        True for yes, False for no
    """
    suffix = " [Y/n] " if default else " [y/N] "

    # Print the prompt to stdout (which goes to terminal even when piped)
    print(prompt + suffix, end="", flush=True)

    try:
        # Try to read from /dev/tty (the controlling terminal)
        # This works even when stdin is a pipe
        with open("/dev/tty", "r") as tty:
            response = tty.readline().strip().lower()
    except (OSError, IOError):
        # /dev/tty not available (e.g., no controlling terminal, or Windows)
        # Fall back to stdin
        try:
            response = input().strip().lower()
        except EOFError:
            print(f"(using default: {'yes' if default else 'no'})")
            return default

    if not response:
        return default
    return response in ("y", "yes")


def load_settings() -> dict:
    """
    Load existing settings.json or return empty dict.

    Returns:
        Parsed settings dict, or empty dict if file doesn't exist

    Raises:
        json.JSONDecodeError: If file exists but contains invalid JSON
    """
    if not SETTINGS_FILE.exists():
        print_info(f"No existing settings file at {SETTINGS_FILE}")
        return {}

    print_info(f"Loading existing settings from {SETTINGS_FILE}")
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)


def save_settings(settings: dict) -> None:
    """
    Save settings to settings.json with pretty formatting.

    Args:
        settings: The settings dict to save
    """
    # Ensure the .claude directory exists
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")  # Trailing newline for POSIX compliance

    print_success(f"Saved settings to {SETTINGS_FILE}")


# =============================================================================
# INSTALL FUNCTIONS
# =============================================================================


def download_hook() -> bool:
    """
    Download the hook script to ~/.claude/hooks/validate-rm.py

    Returns:
        True if downloaded, False if already exists
    """
    # Create hooks directory if it doesn't exist
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)

    if HOOK_FILE.exists():
        print_skip(f"Hook file already exists at {HOOK_FILE}")
        return False

    print_info(f"Downloading hook from {HOOK_URL}")

    try:
        urllib.request.urlretrieve(HOOK_URL, HOOK_FILE)
    except Exception as e:
        print_error(f"Failed to download hook: {e}")
        sys.exit(1)

    # Make the hook executable (chmod +x)
    current_mode = os.stat(HOOK_FILE).st_mode
    os.chmod(HOOK_FILE, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print_success(f"Downloaded hook to {HOOK_FILE}")
    return True


def add_hook_to_settings(settings: dict) -> bool:
    """
    Add the PreToolUse hook configuration to settings.

    This function carefully merges our hook into existing settings:
    - Creates hooks.PreToolUse array if it doesn't exist
    - Finds or creates the Bash matcher entry
    - Adds our hook command if not already present

    Args:
        settings: The settings dict to modify (mutated in place)

    Returns:
        True if hook was added, False if already present
    """
    # Ensure hooks object exists
    # settings.hooks is the top-level hooks configuration
    if "hooks" not in settings:
        settings["hooks"] = {}

    # Ensure PreToolUse array exists
    # PreToolUse hooks run before Claude executes a tool
    if "PreToolUse" not in settings["hooks"]:
        settings["hooks"]["PreToolUse"] = []

    pre_tool_use = settings["hooks"]["PreToolUse"]

    # Look for an existing Bash matcher
    # Each entry in PreToolUse can have a "matcher" to filter which tools it applies to
    bash_matcher = None
    for entry in pre_tool_use:
        if entry.get("matcher") == "Bash":
            bash_matcher = entry
            break

    # If no Bash matcher exists, create one
    if bash_matcher is None:
        bash_matcher = {"matcher": "Bash", "hooks": []}
        pre_tool_use.append(bash_matcher)
        print_info("Created new Bash matcher in PreToolUse")

    # Ensure the matcher has a hooks array
    if "hooks" not in bash_matcher:
        bash_matcher["hooks"] = []

    # Check if our hook command is already present
    # We check by comparing the command string to avoid duplicates
    for hook in bash_matcher["hooks"]:
        if hook.get("command") == HOOK_COMMAND:
            print_skip("Hook already configured in settings")
            return False

    # Add our hook to the Bash matcher
    bash_matcher["hooks"].append(HOOK_CONFIG.copy())
    print_success("Added rm-guard hook to PreToolUse configuration")
    return True


def add_permissions_to_settings(settings: dict) -> list[str]:
    """
    Add ask permissions for rm/unlink/rmdir commands.

    These permissions make Claude Code ask for confirmation before running
    any rm, unlink, or rmdir commands, providing an additional layer of safety.

    Args:
        settings: The settings dict to modify (mutated in place)

    Returns:
        List of permissions that were added (empty if all already existed)
    """
    # Ensure permissions object exists
    if "permissions" not in settings:
        settings["permissions"] = {}

    # Ensure ask array exists
    # The "ask" array lists tool patterns that require user confirmation
    if "ask" not in settings["permissions"]:
        settings["permissions"]["ask"] = []

    ask_permissions = settings["permissions"]["ask"]
    added = []

    # Add each permission if not already present
    for perm in ASK_PERMISSIONS:
        if perm in ask_permissions:
            print_skip(f"Permission already exists: {perm}")
        else:
            ask_permissions.append(perm)
            print_success(f"Added permission: {perm}")
            added.append(perm)

    return added


def install() -> None:
    """
    Main installation routine.

    Steps:
    1. Download the hook script
    2. Ask user about permissions
    3. Load existing settings
    4. Merge in hook configuration
    5. Optionally merge in permissions
    6. Save settings
    """
    print_header("claude-code-rm-guard installer")

    # Step 1: Download the hook script
    print("Step 1: Download hook script")
    download_hook()

    # Step 2: Ask about permissions before loading settings
    # We ask early so the user knows what will happen
    print("\nStep 2: Configure permissions")
    print_info("Ask permissions make Claude request confirmation before running")
    print_info("rm, unlink, or rmdir commands (recommended for extra safety)")
    add_perms = ask_yes_no("Include ask permissions for rm/unlink/rmdir?", default=True)

    # Step 3: Load existing settings
    print("\nStep 3: Load settings")
    try:
        settings = load_settings()
    except json.JSONDecodeError as e:
        print_error(f"Failed to parse {SETTINGS_FILE}: {e}")
        print_error("Please fix the JSON syntax and try again")
        sys.exit(1)

    # Step 4: Add hook configuration
    print("\nStep 4: Configure hook")
    hook_added = add_hook_to_settings(settings)

    # Step 5: Add permissions if requested
    perms_added = []
    if add_perms:
        print("\nStep 5: Configure permissions")
        perms_added = add_permissions_to_settings(settings)
    else:
        print("\nStep 5: Skipping permissions (user declined)")

    # Step 6: Save settings if anything changed
    print("\nStep 6: Save settings")
    if hook_added or perms_added:
        save_settings(settings)
    else:
        print_skip("No changes to save (everything already configured)")

    # Summary
    print_header("Installation complete!")
    print("The rm-guard hook is now active. It will block rm/unlink/rmdir/shred")
    print("commands that target paths outside your working directory.")
    print("\nTo verify, run /hooks in Claude Code to see active hooks.")
    print("\nTo uninstall:")
    print("  curl -fsSL https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/install.py | python3 - --uninstall")


# =============================================================================
# UNINSTALL FUNCTIONS
# =============================================================================


def remove_hook_from_settings(settings: dict) -> bool:
    """
    Remove our hook from the PreToolUse configuration.

    This is non-destructive:
    - Only removes our specific hook command
    - Leaves other hooks in the Bash matcher intact
    - Cleans up empty arrays/objects

    Args:
        settings: The settings dict to modify (mutated in place)

    Returns:
        True if hook was removed, False if not found
    """
    # Check if hooks configuration exists
    if "hooks" not in settings:
        print_skip("No hooks configuration found")
        return False

    if "PreToolUse" not in settings["hooks"]:
        print_skip("No PreToolUse hooks found")
        return False

    pre_tool_use = settings["hooks"]["PreToolUse"]

    # Find the Bash matcher
    bash_matcher = None
    bash_matcher_index = None
    for i, entry in enumerate(pre_tool_use):
        if entry.get("matcher") == "Bash":
            bash_matcher = entry
            bash_matcher_index = i
            break

    if bash_matcher is None:
        print_skip("No Bash matcher found in PreToolUse")
        return False

    # Find and remove our hook command
    hooks = bash_matcher.get("hooks", [])
    hook_index = None
    for i, hook in enumerate(hooks):
        if hook.get("command") == HOOK_COMMAND:
            hook_index = i
            break

    if hook_index is None:
        print_skip("rm-guard hook not found in configuration")
        return False

    # Remove our hook
    hooks.pop(hook_index)
    print_success("Removed rm-guard hook from configuration")

    # Clean up: remove Bash matcher if its hooks array is now empty
    if not hooks:
        pre_tool_use.pop(bash_matcher_index)
        print_info("Removed empty Bash matcher")

        # Clean up: remove PreToolUse if it's now empty
        if not pre_tool_use:
            del settings["hooks"]["PreToolUse"]
            print_info("Removed empty PreToolUse array")

            # Clean up: remove hooks if it's now empty
            if not settings["hooks"]:
                del settings["hooks"]
                print_info("Removed empty hooks object")

    return True


def remove_permissions_from_settings(settings: dict) -> list[str]:
    """
    Remove our ask permissions from settings.

    This removes the specific permission patterns we would have added.
    Note: If the user had these permissions before installation, they
    will also be removed - this is acceptable as the permissions are
    still enforced by Claude Code's built-in system.

    Args:
        settings: The settings dict to modify (mutated in place)

    Returns:
        List of permissions that were removed
    """
    # Check if permissions exist
    if "permissions" not in settings:
        print_skip("No permissions configuration found")
        return []

    if "ask" not in settings["permissions"]:
        print_skip("No ask permissions found")
        return []

    ask_permissions = settings["permissions"]["ask"]
    removed = []

    # Remove each of our permissions if present
    for perm in ASK_PERMISSIONS:
        if perm in ask_permissions:
            ask_permissions.remove(perm)
            print_success(f"Removed permission: {perm}")
            removed.append(perm)
        else:
            print_skip(f"Permission not found: {perm}")

    # Clean up: remove ask if it's now empty
    if not ask_permissions:
        del settings["permissions"]["ask"]
        print_info("Removed empty ask array")

        # Clean up: remove permissions if it's now empty
        if not settings["permissions"]:
            del settings["permissions"]
            print_info("Removed empty permissions object")

    return removed


def delete_hook_file() -> bool:
    """
    Delete the hook script file.

    Returns:
        True if deleted, False if not found
    """
    if not HOOK_FILE.exists():
        print_skip(f"Hook file not found at {HOOK_FILE}")
        return False

    HOOK_FILE.unlink()
    print_success(f"Deleted hook file: {HOOK_FILE}")

    # Clean up: remove hooks directory if empty
    try:
        HOOKS_DIR.rmdir()
        print_info("Removed empty hooks directory")
    except OSError:
        # Directory not empty, that's fine
        pass

    return True


def uninstall() -> None:
    """
    Main uninstallation routine.

    Steps:
    1. Load existing settings
    2. Remove hook from configuration
    3. Remove permissions
    4. Save settings
    5. Delete hook file
    """
    print_header("claude-code-rm-guard uninstaller")

    # Step 1: Load existing settings
    print("Step 1: Load settings")
    try:
        settings = load_settings()
    except json.JSONDecodeError as e:
        print_error(f"Failed to parse {SETTINGS_FILE}: {e}")
        print_error("Please fix the JSON syntax and try again")
        sys.exit(1)

    if not settings:
        print_info("No settings file found, skipping settings cleanup")
        settings_changed = False
    else:
        # Step 2: Remove hook from configuration
        print("\nStep 2: Remove hook configuration")
        hook_removed = remove_hook_from_settings(settings)

        # Step 3: Remove permissions
        print("\nStep 3: Remove permissions")
        perms_removed = remove_permissions_from_settings(settings)

        settings_changed = hook_removed or perms_removed

        # Step 4: Save settings if anything changed
        print("\nStep 4: Save settings")
        if settings_changed:
            save_settings(settings)
        else:
            print_skip("No changes to save")

    # Step 5: Delete hook file
    print("\nStep 5: Delete hook file")
    delete_hook_file()

    # Summary
    print_header("Uninstallation complete!")
    print("The rm-guard hook has been removed.")
    print("\nTo reinstall:")
    print("  curl -fsSL https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/install.py | python3")


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Entry point - parse args and run install or uninstall."""
    # Check for --uninstall flag
    if "--uninstall" in sys.argv or "-u" in sys.argv:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
