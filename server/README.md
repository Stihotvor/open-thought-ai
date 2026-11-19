# Server

Python server. Starts in milestone M2.

## Quality gate

Run the gate with one command. CI and the pre-push hook run the same script.

```bash
server/scripts/gate.sh
```

Enable the pre-push hook once per clone. The hook stops a push when the gate fails. This saves CI minutes.

```bash
git config core.hooksPath .githooks
```

CI runs the gate on each pull request to `main`.
