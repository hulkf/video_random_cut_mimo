# Project Memory

This file records project-specific preferences for `video_random_cut_mimo`.
Follow it when working in this repository.

## CodeGraph

If a `.codegraph/` directory exists at the repo root, use CodeGraph before
grep/find or ad hoc file reads when locating or understanding code. If there is
no `.codegraph/` directory, skip CodeGraph.

## User Preferences

- Work in Chinese by default when reporting progress or results to the user.
- Prefer direct implementation, narrow verification, and concrete usable
  entrypoints over abstract plans.
- Keep edits narrowly scoped to the requested feature or bug. Do not touch
  unrelated dirty files, generated caches, or local config unless needed.
- This is a PyQt desktop video tool. For UI/tab changes, verify the real tab or
  at least run a focused syntax/import/offscreen check when full GUI testing is
  not practical.
- For every code change, default to committing and pushing the narrow task
  files to Git after verification, because the user wants work synced in case
  the machine shuts down. If the worktree contains unrelated changes, do not
  stage them.
- After every code change, explicitly tell the user whether the running project
  needs to be restarted.

## Restart Guidance

- Usually needs restart: Python source changes under `gui/`, `core/`, `utils/`,
  `services/`, `main.py`, startup/import wiring, dependency changes, packaged
  runtime hooks, or anything that affects already-loaded PyQt classes.
- Usually does not need restart: documentation-only changes, comments only,
  files not loaded by the running app, or changes to future packaging scripts
  that are not part of the current running process.
- If unsure, say so plainly and recommend restart. For this desktop app, a
  restart means closing the current app window/process and launching it again
  so Python reloads changed modules.
