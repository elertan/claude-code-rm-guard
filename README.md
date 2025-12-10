# claude-code-rm-guard

**Important to note:**
Claude Code's sandboxing feature (https://code.claude.com/docs/en/sandboxing) seems like a much better way to solve this problem, or otherwise using devcontainers.
If that isn't what you want, then maybe this hook will help!

🛡️ Security hook for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that prevents destructive file operations outside your working directory.

Stop Claude from accidentally running `rm -rf ~/` or other catastrophic commands.

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/install.py | python3
```

The installer will:
1. Download the hook to `~/.claude/hooks/validate-rm.py`
2. Add the hook configuration to `~/.claude/settings.json`
3. Optionally add ask permissions for rm/unlink/rmdir (recommended)

**Uninstall:**
```bash
curl -fsSL https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/install.py | python3 - --uninstall
```

## Features

- **Path Validation**: Blocks `rm`, `unlink`, `rmdir`, and `shred` commands targeting paths outside your project directory
- **Shell-Aware Parsing**: Uses Python's `shlex` with `punctuation_chars=True` for proper handling of pipes, chains, and quoted arguments
- **Recursive Analysis**: Detects dangerous commands hidden in `sudo`, `xargs`, `find -exec`, `bash -c`, and other wrappers
- **Safety-First Blocking**: Commands with unresolvable paths (variables, globs, command substitution) are blocked by default
- **Zero Dependencies**: Pure Python 3, no external packages required

## How It Works

When Claude Code attempts to run a Bash command:

1. **Hook Receives Input**: Claude Code sends JSON via stdin with `tool_name`, `tool_input.command`, and `cwd`
2. **Command Parsing**: The script uses `shlex` to tokenize the command, splitting on `;`, `|`, `&&`, `||`
3. **Recursive Analysis**: Each subcommand is analyzed, unwrapping `sudo`, `bash -c`, etc.
4. **Path Resolution**: Target paths are resolved to absolute paths (handling `~`, relative paths, `..`)
5. **Validation**: Each path is checked against the working directory
6. **Decision**:
   - Exit code `0` → Command proceeds to normal permission flow
   - Exit code `2` → Command is **blocked**, error message shown to Claude

### Example: What happens with `rm -rf ~/`
```
Working directory: /home/user/projects/myapp

1. Claude attempts: Bash(rm -rf ~/)
2. Hook parses: ['rm', '-rf', '~/']
3. Path '~/' expands to '/home/user/'
4. Check: '/home/user/' starts with '/home/user/projects/myapp/'? NO
5. BLOCKED with stderr:
   "BLOCKED: rm targets path outside working directory
    Target: /home/user
    Working directory: /home/user/projects/myapp"
6. Claude sees the error and adjusts its approach
```

## What Gets Blocked

| Command | Result | Reason |
|---------|--------|--------|
| `rm -rf ~/` | ❌ Blocked | Outside working directory |
| `rm -rf /` | ❌ Blocked | Outside working directory |
| `rm test.txt` | ✅ Allowed | Within working directory |
| `rm -rf ../other-project/` | ❌ Blocked | Outside working directory |
| `sudo rm -rf ~/` | ❌ Blocked | `sudo` unwrapped, path validated |
| `bash -c "rm -rf ~/"` | ❌ Blocked | Nested command parsed |
| `find / -exec rm {} \;` | ❌ Blocked | Dynamic paths unresolvable |
| `rm -rf $HOME` | ❌ Blocked | Variable unresolvable (safety) |
| `rm -rf ~/.*` | ❌ Blocked | Glob unresolvable (safety) |
| `echo ~ \| xargs rm -rf` | ❌ Blocked | xargs+rm detected |
| `rm -rf ./src/` | ✅ Allowed | Within working directory |

## Edge Cases Handled

✅ **Properly handles:**
- Chained commands: `cmd1; cmd2 && cmd3 || cmd4`
- Pipes: `echo x | xargs rm`
- Quoted paths: `rm "file with spaces.txt"`
- Tilde expansion: `~/`, `~user/`
- Relative paths: `./`, `../`, `foo/bar`
- Absolute paths: `/home/user/file`
- Path traversal: `./../../etc/passwd`
- Command wrappers: `sudo`, `env`, `nice`, `timeout`, `nohup`
- Shell invocation: `bash -c "..."`, `sh -c "..."`
- Find exec: `find -exec rm {} \;`
- Xargs: `xargs rm`

⚠️ **Cannot catch (inherent limitations):**
- Shell aliases: `alias r=rm; r ~/`
- Shell functions: `del() { rm "$@"; }; del ~/`
- Sourced scripts: `. malicious.sh`
- eval: `eval "rm -rf ~/"`
- Runtime symlink attacks

## Manual Installation

### 1. Download the hook
```bash
mkdir -p ~/.claude/hooks
curl -o ~/.claude/hooks/validate-rm.py \
  https://raw.githubusercontent.com/elertan/claude-code-rm-guard/main/hooks/validate-rm.py
chmod +x ~/.claude/hooks/validate-rm.py
```

### 2. Configure Claude Code

Add to your `~/.claude/settings.json`:
```json
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
```

### 3. (Recommended) Add ask rules for rm commands

Add ask permissions so Claude Code requests confirmation before running rm commands:
```json
{
  "permissions": {
    "ask": ["Bash(rm:*)", "Bash(unlink:*)", "Bash(rmdir:*)"]
  }
}
```

## Configuration Options

### Timeout configuration

Add a timeout (default is 60 seconds):
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/validate-rm.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

## Testing

Run the test suite:
```bash
python3 -m pytest tests/ -v
```

Manual testing:
```bash
# Test a safe command
echo '{"tool_name": "Bash", "tool_input": {"command": "rm test.txt"}, "cwd": "/home/user/project"}' | python3 hooks/validate-rm.py
echo $?  # Should be 0

# Test a dangerous command
echo '{"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/"}, "cwd": "/home/user/project"}' | python3 hooks/validate-rm.py
echo $?  # Should be 2
```

## Troubleshooting

### Hook not running

1. Check hook is loaded: Run `/hooks` in Claude Code
2. Verify JSON syntax in settings file
3. Test script manually: `echo '{"tool_name":"Bash","tool_input":{"command":"rm test"},"cwd":"/tmp"}' | python3 ~/.claude/hooks/validate-rm.py`

### Commands blocked unexpectedly

Enable debug output by modifying the script or checking Claude Code's verbose mode (`Ctrl+O`).

### Hook runs but doesn't block

- Ensure you're using Python 3.6+ (for `shlex` `punctuation_chars` support)
- Check that the script is executable: `chmod +x ~/.claude/hooks/validate-rm.py`

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request

## Security

This hook provides defense-in-depth but is **not a sandbox**. For maximum security:

1. Run Claude Code in a Docker container
2. Use filesystem permissions to protect critical directories
3. Combine with Claude Code's built-in permission system
4. Review all commands before approving

## License

MIT License - see [LICENSE](LICENSE) for details.

## See Also

- [Claude Code Documentation](https://docs.anthropic.com/en/docs/claude-code)
- [Claude Code Hooks Reference](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [Claude Code Plugins](https://code.claude.com/docs/en/plugins)
