# Bootstrap a new repo from this template

This template ships the org baseline: the thin-caller workflow stubs (pinned to
their published `@<name>/stable` channel tags) and the root/baseline files. Most
of it works out of the box. Two things are **per-stack** and must be picked once,
after you create your repo from the template:

## 1. Pick your Dependabot stack

`.github/dependabot.yml` ships with the `frontend` (npm) stack by default.
Replace it with the stack that matches your project — the canonical stacks live
in `petry-projects/.github` under `standards/dependabot/<stack>.yml`. Available
stacks: `frontend` (npm), `backend-go`, `backend-python`, `backend-rust`,
`fullstack` (npm + Go + Terraform), and `infra-terraform`.

## 2. Customize `ci.yml` for your stack

`.github/workflows/ci.yml` ships as a documented **customize-per-stack stub**.
It runs the stack-agnostic checks out of the box; add your build/test/lint steps
for your language and toolchain. (The single-template default keeps one `ci.yml`
that you tailor; per-stack `ci.yml` variants remain an advisory open question and
are not required to start.)

## 3. Set your Sonar project key

If you enable the `sonarcloud` workflow, set `sonar.projectKey` /
`sonar.organization` in `sonar-project.properties` to match your repo.

---

## What this template does NOT do

Two onboarding questions are resolved here so they are not left implicit:

- **Framework subtrees (`frameworks/`) are opt-in — they are not seeded by this
  template.** The agentic frameworks (`bmad-method`, `spec-kit`, `gsd`) are
  `git subtree` development tooling maintained in `petry-projects/.github-private`;
  a product repo does not need them on day 0. If your project uses one, add it on
  demand with `git subtree add` against its upstream — see that repo's `AGENTS.md`.

- **App installs (Claude, CodeRabbit) are a manual org-admin step — the bootstrap
  does not install them.** `bootstrap-new-repo.sh` runs as `GITHUB_TOKEN` and
  cannot install org GitHub Apps; installation is a one-time org-level action by an
  org admin. The bootstrap only *disables those apps' check-suite auto-trigger* on
  the new repo (so queued-but-never-completed suites never block auto-merge),
  assuming the apps are already installed org-wide.

---

The rest — CODEOWNERS, AGENTS.md/CLAUDE.md pointers, LICENSE, SECURITY.md,
.gitignore — is ready as shipped. Repo settings, rulesets, labels, and security
configuration are applied separately by the org `bootstrap-new-repo.sh` flow.