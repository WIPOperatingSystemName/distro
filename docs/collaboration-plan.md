# Maintainer GitOps plan

Contributors can work across all seven repositories. You are the main maintainer
and control merges and releases. Put the contributor quickstart in the public
organization repository `.github`, at `profile/README.md`.

1. **Use the pinned baseline.** Clone distro with all six source submodules.
   Keep ignored build outputs private; fresh CI rebuilds its own toolkits.
2. **Validate PRs.** Run relevant tests and affected builds on disposable Linux
   workers. Protect `main` with required checks and review. Keep PR jobs separate
   from merge, release and signing credentials.
3. **Review with AI.** Supply a bounded diff and qualification scope. Ask for
   reproducible defects and file/line references. Treat PR text as untrusted.
   Run reproductions in isolated workers; submit fixes as new PRs.
4. **Approve and merge.** You approve the exact final commit after CI passes.
   Merge yourself, or let a narrowly scoped bot execute your approval. The bot
   verifies your identity, approved SHA and current checks; new commits need new
   approval. An LLM recommendation or contributor-applied label is insufficient.
5. **Adopt one tested bundle.** Prepare the integration PR before component
   merges, including any required package revision changes. Test exact PR heads
   against current `main`, merge with merge commits, and keep those tested heads
   as distro pins. They become ancestors of component `main`; related SDK and
   consumer changes reach distro together.
6. **Publish test bundles.** Qualify the reviewed distro commit, then package a
   normal `desktop-use` image, matching firmware, source manifest, SHA256 and
   receipts. Provide `run.sh` and `run.cmd` for installed QEMU with private VM
   state. Verify Linux and Windows launchers before advertising support.

The central controller is in the organization's `.github` repository. It provides
AI review and maintainer-approved cross-repository merges, disabled until owner
configuration. Follow its [setup guide](https://github.com/WIPOperatingSystemName/.github/tree/main/automation).
Remote full builds, live AI review and real merge execution remain unverified;
downloadable Windows/Linux test bundles remain pending. GitHub component merges
are sequential; a partial failure leaves distro pins unchanged and supports a
checked retry.

Keep review separate from privileged execution; see
[GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use).
