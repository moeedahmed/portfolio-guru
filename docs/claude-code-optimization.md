# Claude Code Token Optimization — Portfolio Guru Context

## Optimisation Summary

| Change | Before | After | Saving |
|---|---|---|---|
| ~/.claude/CLAUDE.md | ~1,322 tokens | ~392 tokens | **930 tok/session** |
| ~/projects/portfolio-guru/AGENTS.md | ~2,971 tokens | ~632 tokens | **2,340 tok/session** |
| .claudeignore (venv, .bak, *.log, etc.) | Full file contents readable | Blocked | **Prevents accidental large reads** |
| Total baseline per session | **~4,300 tokens** | **~1,024 tokens** | **~3,270 tok/session** |

## Invocation Patterns (effort level routing)

Use the right model and effort level for the task. Don't use Opus `xhigh` for everything.

| Task type | Model | Effort | Invocation |
|---|---|---|---|
| Lint/format/simple one-file edit | `sonnet-4-6` | `low` | `echo "prompt" \| claude -p --model claude-sonnet-4-6 --print --p mb bypassPermissions` |
| Single-file impl, straightforward | `sonnet-4-6` | `medium` | Same as above |
| Multi-file feature, needs planning | `opus-4-8` | `high` (default) | `echo "prompt" \| claude -p --model claude-opus-4-8 --print --pmode bypassPermissions` |
| Complex architecture, refactoring | `opus-4-8` | `xhigh` | Use `CLAUDE_CODE_EFFORT_LEVEL=xhigh` or specify in prompt |
| Frontier research, deepest reasoning | `opus-4-8` | `max` | Reserve for session-critical reasoning only |

## Key Token-Saving Practices

1. **Prefer pipe mode** (`echo | claude -p`) over interactive sessions for one-shot tasks. Interactive sessions keep the conversation context; pipe mode processes and exits.
2. **Use `/compact` in long sessions** — compresses accumulated context instead of starting fresh.
3. **Start fresh for unrelated tasks** — don't carry stale context from a previous feature into the next.
4. **No MCP config override needed** — Claude Code picks up `~/.claude/settings.json` automatically. Only use `--mcp-config` when you need server-specific overrides.
5. **No stream-json with pipe mode** — `--output-format stream-json` requires `--verbose` and is for TUI mode only. Use `--print` alone for pipes.
6. **Prefer targeted reads** — use CodeGraph for structure, `rg` for literal text. Avoid directory-wide `Read` or `Bash cat` that loads thousands of lines into context.

## Session Budget Notes

- Claude Max 100 plan: 5-hour rolling window, shared across claude.ai + Claude Code + Claude Desktop.
- Every conversation on claude.ai counts against the same budget.
- Opus 4.7+ tokenizer inflates tokens by up to 35% vs older models.
- When exhausted: route through AGY (Google subscription) or OpenCode (DeepSeek V4 Pro, free).
