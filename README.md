# hivemap

Live, interactive map of your running Claude Code sessions: subagents, tool calls, and the files they touch.
Reads Claude Code's own files in `~/.claude` (sessions and transcripts); serves only on `127.0.0.1:7777`.

## Use

- In Claude Code: `/hivemap:map` (browser window) or `/hivemap:map pane` (herdr pane beside this one)
- In a terminal: `hivemap` · `hivemap tui` · `hivemap pane` · `hivemap serve` (or `start`) · `hivemap status` · `hivemap stop`
- In the terminal view: `↑↓`/`jk` select a session or agent (its tool calls show on the right), `←→`/space fold,
  `enter` or `1`-`9` jump to the herdr pane, `pgup`/`pgdn` scroll, `f` follow the busy session, `q` quit.
  `tab` moves into the call list: `↑↓` pick a call, `enter` opens its full input and output, `←` goes back.
  Mouse: click selects, click a call to open it, click a selected session to fold, wheel scrolls.
- In the web map, click any tool call (a node, or a row in the side panel) to see its full input and output.
- Sessions waiting on you (a permission prompt or question) turn yellow and sort first, in both views and the
  window title. This comes from herdr's `blocked` state (herdr reads it off the pane; no hook needed).
- Selection is shared: pick a session, agent or call in either view and the other follows (needs the server, which `hivemap tui` starts).
- In the map, click a session and use **Jump to terminal pane** to focus its herdr pane.

## Install

```fish
ln -sf ~/projects/hivemap/bin/hivemap ~/.local/bin/hivemap
ln -sf ~/projects/hivemap/fish/hivemap.fish ~/.config/fish/completions/hivemap.fish
claude plugin marketplace add ~/projects/hivemap
claude plugin install hivemap@hivemap
```

Claude Code runs a cached copy of the plugin. After changing files here, bump `version` in
`.claude-plugin/plugin.json` and run `claude plugin marketplace update hivemap && claude plugin update hivemap@hivemap`.

Check: `python3 server/test_overlay.py`. Port: set `HIVEMAP_PORT`. Log: `~/.local/state/hivemap.log`.
