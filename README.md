# OmniTerm

OmniTerm is an Electron desktop app for an offline terminal simulator. The UI, virtual command interpreter, session lifecycle, and persistence run in JavaScript. The app does not start a host shell or use Python.

## Run from source

Install Node.js 20 or newer, then run:

```sh
npm install
npm start
```

Session state is saved as JSON in Electron's per-user application data directory. On startup, the app attempts a read-only import of existing sessions from `~/.terminal_sim/sessions.sqlite3`. Each session has its own virtual filesystem and process, service, package, and Git state. Temporary sessions expire after 12 hours; permanent sessions do not expire. Settings include theme, default shell, and console text size.

## Virtual commands

The simulator implements common Bash and PowerShell workflows: navigation, listing, creating, reading, writing, copying, moving, and deleting virtual files; pipelines and basic text filtering; session history; simulated processes and services; package metadata; and offline Git commands. Network access, host administration, external programs, and real containers are unavailable. Unsupported commands return a virtual command-not-found response.

The original Python source files are retained as legacy reference files; the Electron app does not import or launch them.
