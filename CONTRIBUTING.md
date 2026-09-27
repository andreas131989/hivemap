# Contributing

Thanks for helping. hivemap is small on purpose, so a few ground rules:

- **Standard library only.** No pip dependencies, no build step, no JS framework. If something needs a
  library, open an issue first.
- **Everything goes through a pull request.** `main` is protected: CI has to pass on Linux and macOS
  before anything merges, maintainers included.
- **Tests come with changes.** Run them from the repo root:

  ```sh
  python3 -m unittest -v
  ```

  They use a throwaway `~/.claude` and a random port, so they never touch your real sessions or a
  running hivemap. `tests/__init__.py` has the helpers (`FakeClaude`, `line`, `tool_use`, ...).
- **Try it for real** if you changed something visible: `bin/hivemap` for the web map, `bin/hivemap tui`
  for the terminal view.

## Layout

| Path | What |
|---|---|
| `server/overlay.py` | Tails Claude Code transcripts, serves state, selection and call details on 127.0.0.1 |
| `server/index.html` | The web map (one file: HTML, CSS, JS) |
| `server/tui.py` | The terminal view |
| `bin/hivemap` | Launcher: start/stop the server, open a view |
| `commands/map.md`, `.claude-plugin/` | The Claude Code plugin |
| `tests/` | The test suite |

## Releasing

Bump `version` in `.claude-plugin/plugin.json`; installed plugins pick it up with
`claude plugin marketplace update hivemap && claude plugin update hivemap@hivemap`.
