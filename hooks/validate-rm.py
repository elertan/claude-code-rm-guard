#!/usr/bin/env python3
"""
validate-rm.py - PreToolUse hook to block rm/unlink commands outside working directory

INSTALLATION:
  1. Save to ~/.claude/hooks/validate-rm.py
  2. chmod +x ~/.claude/hooks/validate-rm.py
  3. Add to ~/.claude/settings.json:
     {
       "hooks": {
         "PreToolUse": [
           {
             "matcher": "Bash",
             "hooks": [
               {
                 "type": "command",
                 "command": "python3 ~/.claude/hooks/validate-rm.py"
               }
             ]
           }
         ]
       }
     }

BEHAVIOR:
  - Parses shell commands using shlex with punctuation_chars=True for proper
    handling of pipes, semicolons, command chains, etc.
  - Recursively analyzes all subcommands in pipes and chains
  - Detects rm, unlink, and other file deletion commands
  - Blocks if ANY target path resolves outside the working directory
  - Blocks commands with unresolvable paths (variables, command substitution)
    as a safety measure

EXIT CODES:
  - 0: Command allowed (still goes through normal permission prompts)
  - 2: Command blocked (stderr shown to Claude)
"""

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Optional


# Commands that can delete files
DANGEROUS_COMMANDS = {
    'rm',           # Standard remove
    'unlink',       # Remove single file
    'rmdir',        # Remove directory
    'shred',        # Secure delete
    '/bin/rm',
    '/usr/bin/rm',
    '/bin/unlink',
    '/usr/bin/unlink',
}

# Commands that execute other commands (need recursive analysis)
COMMAND_EXECUTORS = {
    'xargs',        # xargs rm -rf
    'parallel',     # GNU parallel
    'find',         # find -exec rm {} \;
    'sudo',         # sudo rm
    'doas',         # doas rm
    'env',          # env rm
    'nice',         # nice rm
    'nohup',        # nohup rm
    'time',         # time rm
    'timeout',      # timeout 10 rm
    'watch',        # watch rm
    'sh',           # sh -c "rm ..."
    'bash',         # bash -c "rm ..."
    'zsh',          # zsh -c "rm ..."
    'dash',         # dash -c "rm ..."
    'fish',         # fish -c "rm ..."
}

# Patterns that indicate unresolvable/dynamic content
UNRESOLVABLE_PATTERNS = [
    r'\$\{',        # ${var}
    r'\$\(',        # $(command)
    r'`[^`]+`',     # `command`
    r'\$[A-Za-z_]', # $VAR
    r'\*',          # glob *
    r'\?',          # glob ?
    r'\[.*\]',      # glob [...]
]


def block(message: str) -> None:
    """Print error to stderr and exit with code 2 to block the command."""
    print(message, file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    """Exit with code 0 to allow the command."""
    sys.exit(0)


def resolve_path(path: str, cwd: str, home: str) -> Optional[str]:
    """
    Resolve a path to its absolute canonical form.
    Returns None if the path contains unresolvable elements.
    """
    # Check for unresolvable patterns
    for pattern in UNRESOLVABLE_PATTERNS:
        if re.search(pattern, path):
            return None
    
    # Expand ~ and ~user
    if path.startswith('~'):
        if path == '~' or path.startswith('~/'):
            path = home + path[1:]
        else:
            # ~otheruser - we can't safely resolve this
            return None
    
    # Convert to absolute path
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    
    # Normalize the path (resolve .. and . but don't follow symlinks yet)
    # Using os.path.normpath to handle .. traversal
    path = os.path.normpath(path)
    
    # For security, also try to resolve symlinks if the path exists
    # This catches symlink attacks like: ln -s /home/user ~/safe/link; rm ~/safe/link/../..
    try:
        if os.path.exists(path):
            path = os.path.realpath(path)
    except OSError:
        pass
    
    return path


def is_path_within_directory(path: str, directory: str) -> bool:
    """Check if path is within or equal to directory."""
    # Normalize both paths
    path = os.path.normpath(path)
    directory = os.path.normpath(directory)
    
    # Check if path starts with directory
    # Add trailing separator to avoid /home/user matching /home/username
    if path == directory:
        return True
    return path.startswith(directory + os.sep)


def parse_command_tokens(command: str) -> list[list[str]]:
    """
    Parse a shell command into a list of simple commands.
    Each simple command is a list of tokens.
    Handles pipes, semicolons, &&, ||, etc.
    """
    try:
        # Use shlex with punctuation_chars=True for proper shell parsing
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = False
        tokens = list(lexer)
    except ValueError as e:
        # Malformed command (unclosed quotes, etc.)
        # Block as a safety measure
        return None
    
    # Split on shell operators into separate commands
    commands = []
    current_command = []
    
    i = 0
    while i < len(tokens):
        token = tokens[i]
        
        # Check for command separators
        if token in (';', '|', '||', '&&', '&', '\n'):
            if current_command:
                commands.append(current_command)
                current_command = []
        # Check for redirections (skip the next token which is the filename)
        elif token in ('>', '>>', '<', '2>', '2>>', '&>', '&>>'):
            # Include redirection target in current command for path checking
            if i + 1 < len(tokens):
                current_command.append(token)
                current_command.append(tokens[i + 1])
                i += 1
            else:
                current_command.append(token)
        else:
            current_command.append(token)
        
        i += 1
    
    if current_command:
        commands.append(current_command)
    
    return commands


def extract_rm_targets(tokens: list[str], cwd: str, home: str) -> tuple[list[str], list[str]]:
    """
    Extract target paths from an rm-like command.
    Returns (resolved_paths, unresolvable_reasons)
    """
    if not tokens:
        return [], []
    
    resolved_paths = []
    unresolvable_reasons = []
    
    cmd = os.path.basename(tokens[0])
    
    # Handle command wrappers that execute subcommands
    if cmd in ('sudo', 'doas', 'env', 'nice', 'nohup', 'time', 'timeout'):
        # Find where the actual command starts (after flags and sudo's arguments)
        sub_start = 1
        while sub_start < len(tokens):
            if tokens[sub_start].startswith('-'):
                # Skip flag and potentially its argument
                if cmd == 'sudo' and tokens[sub_start] in ('-u', '-g', '-C'):
                    sub_start += 2
                elif cmd == 'timeout' and sub_start == 1:
                    # timeout's first arg after flags is the duration
                    sub_start += 1
                else:
                    sub_start += 1
            elif '=' in tokens[sub_start] and cmd == 'env':
                # env VAR=value
                sub_start += 1
            else:
                break
        
        if sub_start < len(tokens):
            return extract_rm_targets(tokens[sub_start:], cwd, home)
        return [], []
    
    # Handle shell -c "command"
    if cmd in ('sh', 'bash', 'zsh', 'dash', 'fish'):
        for i, token in enumerate(tokens[1:], 1):
            if token == '-c' and i + 1 < len(tokens):
                # Parse the command string
                nested_commands = parse_command_tokens(tokens[i + 1])
                if nested_commands is None:
                    unresolvable_reasons.append(f"Malformed nested command in {cmd} -c")
                else:
                    for nested_cmd in nested_commands:
                        paths, reasons = extract_rm_targets(nested_cmd, cwd, home)
                        resolved_paths.extend(paths)
                        unresolvable_reasons.extend(reasons)
                return resolved_paths, unresolvable_reasons
        return [], []
    
    # Handle xargs
    if cmd == 'xargs':
        # xargs passes stdin to the command, we can't know what paths
        # Check if the command being run is rm
        sub_start = 1
        while sub_start < len(tokens) and tokens[sub_start].startswith('-'):
            sub_start += 1
        
        if sub_start < len(tokens):
            subcmd = os.path.basename(tokens[sub_start])
            if subcmd in DANGEROUS_COMMANDS or subcmd == 'rm':
                unresolvable_reasons.append("xargs with rm - paths come from stdin")
        return resolved_paths, unresolvable_reasons
    
    # Handle find -exec/-execdir
    if cmd == 'find':
        # Look for -exec or -execdir followed by rm
        for i, token in enumerate(tokens):
            if token in ('-exec', '-execdir', '-ok', '-okdir'):
                if i + 1 < len(tokens):
                    exec_cmd = os.path.basename(tokens[i + 1])
                    if exec_cmd in DANGEROUS_COMMANDS or exec_cmd == 'rm':
                        # The {} placeholder means paths are dynamic
                        unresolvable_reasons.append(f"find {token} with rm - paths are dynamic")
        return resolved_paths, unresolvable_reasons
    
    # Not an rm-like command
    if cmd not in DANGEROUS_COMMANDS and cmd != 'rm':
        return [], []
    
    # Parse rm arguments
    # rm flags that take no argument: -r, -R, -f, -i, -I, -d, -v, --recursive, etc.
    # rm flags that take an argument: --interactive=WHEN, etc.
    i = 1
    while i < len(tokens):
        token = tokens[i]
        
        # End of options
        if token == '--':
            i += 1
            break
        
        # Long option with argument
        if token.startswith('--') and '=' in token:
            i += 1
            continue
        
        # Long option without argument
        if token.startswith('--'):
            i += 1
            continue
        
        # Short options (can be combined: -rf)
        if token.startswith('-') and len(token) > 1 and token[1] != '-':
            i += 1
            continue
        
        # This is a path argument
        break
    
    # Everything from i onwards is a path
    for path_arg in tokens[i:]:
        resolved = resolve_path(path_arg, cwd, home)
        if resolved is None:
            unresolvable_reasons.append(f"Unresolvable path: {path_arg}")
        else:
            resolved_paths.append(resolved)
    
    return resolved_paths, unresolvable_reasons


def check_command(command: str, cwd: str, home: str, abs_cwd: str) -> Optional[str]:
    """
    Check if a command contains dangerous rm operations.
    Returns an error message if blocked, None if allowed.
    """
    # First, check for obvious dangerous patterns that shlex might not catch
    dangerous_direct_patterns = [
        (r'rm\s+.*[;&|].*rm', 'Multiple rm commands detected'),
    ]
    
    for pattern, reason in dangerous_direct_patterns:
        if re.search(pattern, command):
            # Continue to full analysis, just flagging
            pass
    
    # Parse into simple commands
    commands = parse_command_tokens(command)
    
    if commands is None:
        return f"BLOCKED: Malformed command (unclosed quotes or syntax error)\n  Command: {command}"
    
    all_resolved_paths = []
    all_unresolvable = []
    
    for cmd_tokens in commands:
        if not cmd_tokens:
            continue
        
        # Get the base command name
        base_cmd = os.path.basename(cmd_tokens[0])
        
        # Check if this is a dangerous command or might contain one
        if base_cmd in DANGEROUS_COMMANDS or base_cmd in COMMAND_EXECUTORS:
            paths, unresolvable = extract_rm_targets(cmd_tokens, cwd, home)
            all_resolved_paths.extend(paths)
            all_unresolvable.extend(unresolvable)
    
    # If there are unresolvable paths, block as a safety measure
    if all_unresolvable:
        reasons = '\n  - '.join(all_unresolvable)
        return (
            f"BLOCKED: Command contains rm with unresolvable paths (safety block)\n"
            f"  Reasons:\n  - {reasons}\n"
            f"  Command: {command}\n"
            f"  Working directory: {abs_cwd}"
        )
    
    # Check each resolved path
    for path in all_resolved_paths:
        if not is_path_within_directory(path, abs_cwd):
            return (
                f"BLOCKED: rm targets path outside working directory\n"
                f"  Target: {path}\n"
                f"  Working directory: {abs_cwd}\n"
                f"  Command: {command}"
            )
    
    return None


def main():
    # Read JSON input from stdin
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        block(f"ERROR: Invalid JSON input: {e}")
    
    # Extract fields
    tool_name = input_data.get('tool_name', '')
    tool_input = input_data.get('tool_input', {})
    command = tool_input.get('command', '')
    cwd = input_data.get('cwd', '')
    
    # Only process Bash tool calls
    if tool_name != 'Bash':
        allow()
    
    # Skip if no command
    if not command:
        allow()
    
    # Get home directory
    home = os.environ.get('HOME', os.path.expanduser('~'))
    
    # Resolve working directory
    if not cwd or not os.path.isdir(cwd):
        block(f"ERROR: Invalid or missing working directory: {cwd}")
    
    try:
        abs_cwd = os.path.realpath(cwd)
    except OSError as e:
        block(f"ERROR: Cannot resolve working directory: {e}")
    
    # Quick check: does the command even contain rm-like commands?
    has_dangerous = False
    for cmd in DANGEROUS_COMMANDS:
        # Use word boundary check
        if re.search(rf'\b{re.escape(cmd)}\b', command):
            has_dangerous = True
            break
    
    if not has_dangerous:
        # Also check for command executors that might run rm
        for cmd in COMMAND_EXECUTORS:
            if re.search(rf'\b{re.escape(cmd)}\b', command):
                has_dangerous = True
                break
    
    if not has_dangerous:
        allow()
    
    # Full command analysis
    error = check_command(command, cwd, home, abs_cwd)
    
    if error:
        block(error)
    
    # Command is safe
    allow()


if __name__ == '__main__':
    main()
