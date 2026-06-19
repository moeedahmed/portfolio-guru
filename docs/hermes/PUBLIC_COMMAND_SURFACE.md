# Hermes test bot — public command surface

The Hermes test bot (`@portfolio_guru_test_bot`) runs on the shared
Hermes gateway runtime. Its Telegram slash-command menu (the list shown
when a user types `/`) is **not** controlled by the Portfolio Guru repo
or by the profile `config.yaml`. It is built from a global, hardcoded
registry in the Hermes runtime.

## Where the menu comes from

```
hermes-agent/hermes_cli/commands.py
  COMMAND_REGISTRY            # hardcoded list of every slash command
  telegram_bot_commands()     # derives (name, description) pairs from it
  telegram_menu_commands()    # caps/prioritises for the visible menu
        │
        ▼
hermes-agent/gateway/platforms/telegram.py
  await self._bot.set_my_commands(bot_commands, scope=…)   # pushes to Telegram
```

The only per-command config knob is `gateway_config_gate`, and it works
in the **opposite** direction to what we need: it _reveals_ CLI-only
commands when a config value is truthy. There is no per-profile denylist
or allowlist that would _hide_ a built-in command from the menu.

## The problem (verified)

As shipped, the runtime pushes **51** commands to the public menu,
including operator/maintenance commands that are inappropriate for a
clinician-facing bot. All eight commands the product brief calls out are
present:

```
model, debug, restart, agents, rollback, usage, approve, deny
```

## What this repo owns

`backend/hermes_command_surface.py` is the product-owned source of truth
for what the public menu _should_ be. It intentionally matches the
current beta bot menu:

- `PUBLIC_COMMANDS` — the allowlist (ordered, with product-facing
  descriptions): `start`, `settings`, `cancel`, `reset`, `help`.
- `FORBIDDEN_COMMANDS` — the admin/runtime denylist (the eight named
  commands plus other operator/maintenance/billing commands).
- `filter_menu(candidates)` — reduces any runtime menu to the public
  surface when those commands are present in the candidate list.
- `validate_manifest()` — internal integrity checks.

Tests in `backend/tests/test_hermes_command_surface.py` prove the
forbidden commands are excluded and the product commands are present, and
(when `hermes_cli` is importable) cross-check that every manifest name is
a real runtime command and that `filter_menu` strips the admin commands
from the _actual_ runtime menu.

Some of these are product commands rather than Hermes runtime built-ins.
That is deliberate: the public Telegram command menu is a product UX
surface, not an operator console. If Hermes does not yet handle one of
these commands directly, the next implementation step is to add a
product handler, not to expose `/model`, `/debug`, or `/restart`.

## Blocked runtime hook (what is needed to fix the live menu)

Making the _live_ public menu match this manifest requires a change in
protected global Hermes runtime — which this repo must not edit, and
which would require a gateway restart (out of scope here):

> **`hermes-agent/hermes_cli/commands.py :: telegram_menu_commands()`**
> should read a per-profile menu denylist/allowlist from `config.yaml`
> (e.g. `telegram.menu_command_denylist` or `telegram.menu_commands`) and
> apply it before returning the menu — ideally by delegating to a
> profile-supplied manifest equivalent to `filter_menu` here.

Until that hook exists, this manifest is the declared contract and the
verification point; the live menu is changed only by the Hermes
maintainer. No live action is taken from this repo (no token read, no
`set_my_commands` call, no gateway restart).
