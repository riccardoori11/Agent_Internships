# Run in the cloud with GitHub Actions

The prepared workflow at `.github/workflows/internships.yml` runs the existing Python collector on a GitHub-hosted Ubuntu runner. GitHub supplies the machine, so your computer can be switched off. Keep a hosted runner such as `ubuntu-latest`; a self-hosted runner installed on your computer would still need that computer online. See [GitHub-hosted runners](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners).

Each run checks out the repository, collects live listings, and commits the updated `internship.md` and `data/last-run.json` back to the repository. The committed report provides the previous results for the next run. A temporary runner's local disk alone would not preserve them. The workflow never imports the initial web-assisted search archive.

## Activate and verify

The files in this checkout do **not** establish an active cloud schedule. No GitHub repository is connected here and no hosted workflow run has been verified.

1. Create a GitHub repository with your preferred visibility. Upload this project's code, configuration, tests, documentation and workflow to its **default branch**, usually `main`. Include the hidden `.github/workflows/internships.yml` file, `scripts/`, `internship_finder/`, `config.toml`, and the current `internship.md`. Including `data/last-run.json` preserves the initial run record. Exclude `.venv/`, `__pycache__/`, `.internship-finder.lock`, logs, and environment-owned `.agents/`, `.aws/` and `.codex/` directories. If uploading from a normal Git checkout, commit the prepared files and push them to that repository; this agent's managed `.git` directory is not a usable checkout.
2. Open the repository's **Actions** tab and enable Actions if GitHub asks. The workflow must be present on the default branch for its schedule to trigger. Forks may require explicitly enabling their workflows. [Scheduled workflow rules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
3. Check repository and organization Actions policies allow the actions used by this workflow and its requested `contents: write` permission. The built-in `GITHUB_TOKEN` normally handles report commits; no manually created token is needed. See [token permissions](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token).
4. Select the internship workflow in **Actions**, choose **Run workflow**, and select the default branch. Open the resulting run and check the collection logs, report publication, and final status. Inspect the latest report commit and the timestamps in `internship.md` and `data/last-run.json`. A workflow file appearing in the repository is insufficient verification.
5. After the next scheduled time, verify that an additional run has event **schedule** and that its report commit appears. This confirms automatic triggering rather than just a successful manual run.

Required pull requests, signed commits, or restricted push rules can reject the automated report commit even when the token has write permission. Review the specific rule with the repository owner; do not force-push past it. If your repository requires reviewed changes, publication needs a separate approved output branch or storage destination rather than this workflow's direct report commit. See [protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

## Time and missed runs

The schedule uses native timezone support:

```yaml
on:
  schedule:
    - cron: '0 8 * * *'
      timezone: 'Europe/Paris'
  workflow_dispatch:
```

This expresses 08:00 Paris local time throughout CET and CEST; the local time does not need a seasonal UTC edit. GitHub documents the `timezone` field in [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).

GitHub schedules are best effort: runs can be delayed or dropped under heavy load, especially at the start of an hour. Public repositories can have schedules disabled after 60 days without repository activity. Check the Actions page if reports stop. This workflow has no persistent catch-up queue: a dropped or disabled run does not replay automatically. Use **Run workflow** to recover, or wait for the next enabled daily trigger. These limits are documented under [schedule events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). If execution timing and catch-up guarantees are essential, run the existing systemd timer on an always-online server instead.

## Results and failures

Read the report directly on GitHub from any device. A cloud run updates the repository copy, not a file on a switched-off computer. In a cloned local checkout, use `git pull --ff-only` when you next connect to retrieve the report. Resolve any local edits before pulling.

The collector's usual status rules remain in effect: exit 0 is complete, exit 2 is incomplete, and exit 1 is failure. Partial reports must still be published. On total collection failure the collector adds a stale banner while retaining prior results; the workflow must publish those files before reporting the failed collection. Check both the run's final status and `data/last-run.json`: a readable report can intentionally contain partial or stale results. Push failures are publication failures and do not establish that the repository's report was updated.

Once this hosted schedule is verified, disable an existing local timer if you previously activated it: `systemctl --user disable --now internship-finder.timer`. The local timer was blocked in this agent environment, so no active local timer was established here.
