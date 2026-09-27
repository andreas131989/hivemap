---
description: Open the live map of your Claude Code sessions, agents, tool calls and files
argument-hint: "[pane|start|stop|status]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/hivemap:*)
---

!`"${CLAUDE_PLUGIN_ROOT}/bin/hivemap" $ARGUMENTS`

Relay the line above to the user in one short sentence. If it reports an error, show the log path it names. Do nothing else.
