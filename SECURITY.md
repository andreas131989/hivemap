# Security

hivemap reads your private Claude Code transcripts and serves them on `127.0.0.1`. Bugs that could expose
that data to another site, another user on the machine, or the network matter a lot.

**Please report vulnerabilities privately** through GitHub's
[private vulnerability reporting](https://github.com/andreas131989/hivemap/security/advisories/new),
not in a public issue. You'll get a reply within a few days.

What the server already does, so reports can focus on gaps:
- listens on 127.0.0.1 only, and refuses requests whose `Host` isn't `127.0.0.1:<port>` or
  `localhost:<port>` (DNS rebinding)
- accepts POSTs only as `application/json`, which a cross-origin page can't send without a CORS
  preflight the server never grants
- never opens files from request input; call details come from transcripts it already tails
- escapes everything it puts into the page's HTML, and forbids framing
