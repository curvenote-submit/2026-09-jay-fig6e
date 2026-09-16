---
name: mystmd-site
title: MyST Site Skill
description: General-purpose MyST CLI workflow for multi-page sites — initialize a project, manage `myst.yml` (project metadata, table of contents, theme), build, preview locally, and deploy to a static host. Use when an agent needs to author or maintain a MyST site beyond a single published figure. For the narrow case of surfacing one figure from a notebook, prefer `myst-figure-creator`.
keywords:
  - myst
  - mystmd
  - site
  - book
  - project
  - toc
  - theme
  - build
  - start
  - preview
  - deploy
  - github-pages
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
---

## Overview

MyST sites are directories with a `myst.yml` (project + site config) and one or more Markdown / notebook pages. This skill covers the full local lifecycle: initialize, edit `myst.yml`, build, preview, and ship to a static host. It does **not** author content for specific pages or handle figure-from-notebook publication — see `myst-figure-creator` for that narrow case.

## Required Inputs

| Input | Required | Description |
|---|---|---|
| site root | yes | absolute path where `myst.yml` lives (or should live) |
| action | yes | one of `init`, `build`, `start`, `add-page`, `set-toc`, `set-theme`, `deploy` |
| pages | conditional | required for `init` and `add-page`: ordered list of page filenames or `{file, title}` objects |
| theme | conditional | required for `set-theme`: `book-theme` or `article-theme` |
| deploy target | conditional | required for `deploy`: `github-pages`, `static`, or a custom path; defaults to `static` |
| port | optional | for `start`: dev-server port; defaults to MyST's own default (`3000` or next free) |

When used standalone, these inputs come from the user or the agent's prompt. When used within an orchestrator, the calling step should supply them explicitly.

## Outputs

| Output | Description |
|---|---|
| myst.yml | created or updated project config at `<site root>/myst.yml` |
| pages | any new page files written under `<site root>/` |
| build output | path to `_build/` (or the configured output directory) after a successful `build` or `deploy` |
| preview command | for `start`: the URL the dev server is serving (e.g., `http://localhost:3000`) |
| deploy status | for `deploy`: target + path + any next-step instructions |

## `myst.yml` shape

The fields this skill writes:

```yaml
version: 1
project:
  title: «Project title»
  authors:
    - name: «Author name»
      email: «author@example.com»
  license: «SPDX id»          # optional
  github: «https://github.com/owner/repo»  # optional
  toc:
    - file: index.md
    - title: «Chapter»
      file: chapter.md
      # or a section grouping:
    - title: «Part»
      children:
        - file: part-1.md
        - file: part-2.md
site:
  template: book-theme        # or article-theme
  options:
    favicon: ""               # optional
    logo: ""                  # optional
    logo_text: ""             # optional
```

Always preserve fields the skill didn't write (e.g., `project.keywords`, custom `site.options`). Re-write minimally; do not reformat unrelated parts of the file.

## Steps

Branch on `action`. Each branch ends with a Report step.

### `init`

1. Verify `mystmd` is on PATH (`mystmd --version`). If missing, surface the install command (`npm i -g mystmd`) and stop — do not install globally without consent.
2. If `<site root>/myst.yml` already exists, ask whether to abort, merge (treat as `add-page` + `set-toc`), or overwrite. Default to abort.
3. Create `<site root>/` if missing.
4. Write `myst.yml` from the shape above with the caller-supplied `title`, an empty `authors:` list (unless supplied), and a `toc` listing the `pages` input. The first page becomes `index.md` if not already named that.
5. For each entry in `pages` that doesn't already exist on disk, write a minimal page:
   ```markdown
   ---
   title: «page title»
   ---

   # «page title»

   _Replace this placeholder with content._
   ```
6. Run `mystmd build` once to confirm the scaffold builds. Report any errors verbatim.

### `add-page`

1. Read `<site root>/myst.yml`; abort if missing (the caller should run `init` first).
2. For each new page in `pages`: write the file if absent (template above), append to `project.toc` preserving any nesting the caller passed in.
3. Re-read the file to confirm the edit applied; run `mystmd build` to surface broken references.

### `set-toc`

1. Read `<site root>/myst.yml`.
2. Replace `project.toc` with the supplied ordered list. Preserve `project.title`, `authors`, etc.
3. Verify every file referenced in the new ToC exists on disk; abort with a clear error if any are missing.
4. Run `mystmd build` to confirm.

### `set-theme`

1. Read `<site root>/myst.yml`.
2. Update `site.template` to the supplied theme name. Preserve `site.options` unless the new theme requires removing incompatible keys.
3. Run `mystmd build` to confirm the theme switch renders.

### `build`

1. Verify `mystmd` available.
2. From `<site root>`, run `mystmd build`. Capture stdout + stderr.
3. If the project includes notebooks and the caller wants them executed, run `mystmd build --execute` instead — flag this in the report so the caller knows execution happened.
4. Report the `_build/` path and any warnings (especially "unresolved reference" lines, which usually mean a broken cross-link).

### `start`

1. Verify `mystmd` available.
2. From `<site root>`, run `mystmd start` (long-running). If a port was supplied, append `--port <port>`.
3. Watch stdout for the served URL. Surface it and the kill instruction (`Ctrl-C`) to the caller. Do not block on this process indefinitely in an automated context — for non-interactive callers, treat `start` as "report the command, don't actually launch it".

### `deploy`

1. Verify `mystmd` available.
2. Branch on `deploy target`:
   - **`static`**: run `mystmd build` and report the `_build/` path; the caller copies the directory wherever they want.
   - **`github-pages`**: run `mystmd build` to produce the static output, then ensure a `.github/workflows/` action exists (or scaffold a minimal one — see Edge Cases). Push is the caller's responsibility; do not push from here.
   - **custom path**: build, then copy `_build/` to the target path; do not delete the target without explicit confirmation.
3. Report what was built and what the caller still has to do (push, configure DNS, etc.).

## Page authoring conventions

- Always include `title:` in page frontmatter so the page renders an `<h1>` and a `<title>` tag.
- Cross-reference other pages via Markdown links: `[See chapter 2](./chapter-2.md)` or by label `[](#section-label)`.
- For figures embedded from a notebook elsewhere in the project: `![caption](#fig-label)` (see `myst-figure-creator` for the full pattern).
- Keep pages in the project root or one level deep; deep nesting confuses default themes.

## Edge Cases

- **`mystmd` not installed.** Surface the install command (`npm i -g mystmd` or per-project equivalent). Do not install globally without consent.
- **`myst.yml` version mismatch.** Read `version:` first; if it's not `1`, ask before editing — schema changes between major versions.
- **ToC references a file that doesn't exist.** `mystmd build` will fail. Report which file is missing rather than auto-creating an empty page.
- **Port already in use on `start`.** MyST will pick the next free port; surface the actual URL from stdout rather than assuming the requested port.
- **GitHub Pages deployment.** A minimal workflow file looks like:
  ```yaml
  name: deploy
  on:
    push:
      branches: [main]
  jobs:
    deploy:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-node@v4
          with: { node-version: 20 }
        - run: npm i -g mystmd
        - run: mystmd build --html
        - uses: actions/upload-pages-artifact@v3
          with: { path: ./_build/html }
        - uses: actions/deploy-pages@v4
  ```
  Scaffold this only if it doesn't already exist and the caller confirms — do not overwrite existing workflows.
- **Long-running `start`.** In agent contexts, prefer reporting the command for the user to run rather than spawning a server you can't supervise. Use `Bash` `run_in_background` only if the caller explicitly asked you to launch the preview.
- **Custom theme.** If the caller supplies a theme name the skill doesn't recognise, write it through anyway and let `mystmd build` validate. Surface any "unknown template" errors verbatim.
- **Site uses notebooks but build wasn't run with `--execute`.** Output will show cell sources without outputs. Surface a note: "re-run with `--execute` (or pre-execute via `python-markdown-notebook`) if the rendered figures are blank."

## Validation

After any action's build step:

- `<site root>/myst.yml` parses as YAML; required keys (`version`, `project.title`, `project.toc`) are present.
- `mystmd build` exited 0.
- `_build/` (or configured output) exists and contains at least one `.html` file per page in `project.toc`.

Report `ok` only if all three hold. Otherwise report the most specific failure with the build log excerpt.
