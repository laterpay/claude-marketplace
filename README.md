# supertab-claude-marketplace

Supertab's private plugin marketplace for Claude Code. It currently contains one plugin:

| Plugin | What it does | Docs |
|---|---|---|
| `keeptabs` | Shows what Claude Code is spending, stops it at limits you set, and gives the agent the numbers it needs to stay inside a budget (Supertab proof of concept). | [plugins/keeptabs/README.md](plugins/keeptabs/README.md) |

## Add the marketplace

The repo is private, so Claude Code fetches it with your own git credentials. You need
either an SSH key GitHub knows, or the GitHub CLI:

```bash
gh auth login && gh auth setup-git
```

(With HTTPS only, also set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` to skip the SSH attempt.)
No tokens are stored in this repo.

In Claude Code:

```
/plugin marketplace add jmcodingde/supertab-claude-marketplace
/plugin install <plugin>@supertab
```

or from a shell: `claude plugin marketplace add jmcodingde/supertab-claude-marketplace`, then
`claude plugin install <plugin>@supertab`. Each plugin's README covers what
happens after install.

Claude Code v2.1.275 or later can do both in one step, inside a session:
`/plugin install <plugin> --marketplace jmcodingde/supertab-claude-marketplace`.

## Updates

Plugins here set no `version`, so every commit to `main` is a new version. To pull one:

```bash
claude plugin marketplace update supertab
```

```bash
claude plugin update <plugin>@supertab
```

Then start a new session (or run `/reload-plugins`).

Automatic updates are off by default for custom marketplaces. To turn them on: `/plugin`
→ Marketplaces → supertab → Enable auto-update. Claude Code then checks
at startup, and an update applies from the next session.

## Remove

```bash
claude plugin uninstall <plugin>@supertab
```

```bash
claude plugin marketplace remove supertab
```

Some plugins change things outside Claude Code's plugin folders; their READMEs say how
to undo that. Do it before uninstalling.

## Adding a plugin

1. Put it in `plugins/<name>/`, with a `.claude-plugin/plugin.json` manifest and its
   components (`skills/`, `hooks/hooks.json`, `commands/`, `agents/`, scripts referenced
   via `${CLAUDE_PLUGIN_ROOT}`). Keep everything plugin-specific, including its README,
   inside that folder.
2. Add an entry to `plugins` in `.claude-plugin/marketplace.json`:
   `{"name": "<name>", "source": "./plugins/<name>", "description": "..."}`.
3. Add a row to the table above.
4. Check it: `claude plugin validate .`

Data a plugin must keep across updates belongs in `${CLAUDE_PLUGIN_DATA}`, or in its own
folder under the user's home. Never inside the plugin folder, which is replaced on every
update.
