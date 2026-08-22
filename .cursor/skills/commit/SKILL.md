---
name: commit
description: >-
  Create scoped git commits from the current conversation (e.g. /commit).
  Splits logical changes, never blind full-folder commits. Use when the user
  says /commit or asks to commit session work.
disable-model-invocation: true
---

# Commit

Parse `/commit` or a plain **commit** request the same way.

Commits are for **git history** (bisect, review). User-facing release text goes in [`CHANGELOG.md`](../../CHANGELOG.md) **`## [Unreleased]`** — skill **`peaky-changelog`**. See [CONTRIBUTING.md](../../CONTRIBUTING.md) § Commit messages.

## Scope

- Use **conversation context** to decide what belongs in each commit.
- **Only commit changes from the current conversation** — leave unrelated WIP unstaged.
- Stage paths explicitly (`git add <path>…`); never `git add .` or `git add -A` unless the user asks to commit everything.
- Make **as many logical commits as reasonable** (separate by type, scope, or unrelated work).
- User-visible ships → include `CHANGELOG.md` Unreleased bullet in the same commit when applicable.
- Never commit `output/` meshes, build artifacts, or other generated files unless explicitly part of the task.
- If a changed file is ambiguous (possible work from another session), **ask** before staging.

## Git safety

- NEVER update git config
- NEVER run destructive/irreversible git commands unless the user explicitly requests them
- NEVER skip hooks unless the user explicitly requests it
- NEVER force-push to main/master
- Avoid `git commit --amend` unless all amend rules in user instructions are met
- NEVER commit unless the user asked (this skill satisfies that)

## Procedure

1. In parallel, run:
   - `git status`
   - `git diff` (staged and unstaged)
   - `git log --oneline -8`
2. Map each changed file to **this conversation**. Leave everything else unstaged.
3. Draft one or more Conventional Commit messages (subject **under 50 characters**).
4. Stage only the scoped files (or hunks), then commit each logical group sequentially.
5. Run `git status` after the last commit to verify success.

## Message format

```
<type>(<scope>): <description>
```

- **type** (required): `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`, `perf`
- **scope** (optional): `preset`, `serve`, `splatter`, `geo`, `finder`, `cli`, `assets`, …
- **description**: imperative mood, lowercase, no trailing period

Examples:

```text
feat(serve): add goal-seek map UI
fix(splatter): handle void DEM tiles
perf(seek): trim peak scan to hop disc
chore(ci): add release workflow
```

**Don't:** subject ≥ 50 characters; vague subjects (`update stuff`, `wip`); sentences or past tense (`Fixed the cache.`); non-conventional prefixes.

Before committing, count characters in the full subject. If over 50, shorten scope or description — don't drop the conventional prefix.

Pass messages via HEREDOC:

```bash
git commit -m "$(cat <<'EOF'
subject here

EOF
)"
```

## Report

Summarize commits created (hash + subject) and list anything left unstaged, with a short reason.
