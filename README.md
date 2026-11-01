# Paris C++ internship finder

Collects public job listings, filters for internships with meaningful C++ work in Paris, checks employer application pages, and atomically replaces [internship.md](internship.md). Python 3.11+ and system IANA timezone data are the only runtime dependencies. No API keys, accounts or paid search APIs are needed.

The initial real search was performed on **1 November 2026**, using the web research tool because this agent's shell cannot resolve external hosts. Its evidence archive is [data/bootstrap/search.json](data/bootstrap/search.json), with source observations and links in [data/bootstrap/provenance.json](data/bootstrap/provenance.json). The command below attempted every configured live source with retries, then imported that explicitly requested archive to create the initial report. The report is **incomplete**, and its Unverified section is intentional. Web results may reflect an index cache; the verification time records the retrieval attempt, not an assertion of continuous availability.

**The daily schedule is prepared but not active here.** The project now offers a GitHub-hosted schedule so your computer can be off, as well as a local systemd timer. Neither has been activated from this workspace. The local installer was blocked by systemd D-Bus/crontab restrictions; [deploy/schedule-status.json](deploy/schedule-status.json) records that attempt. The hosted workflow must first be uploaded to a GitHub repository and enabled there.

## Setup and manual run

From a network-enabled Linux host:

```sh
cd /home/riccardo/agent
python3 --version  # 3.11 or newer
python3 -m internship_finder --config config.toml
```

An optional virtual environment needs no dependency installation for this command:

```sh
python3 -m venv .venv
.venv/bin/python -m internship_finder --config config.toml
```

`pip install .` optionally installs an `internship-finder` command; this packaging step may download the setuptools build tool. It is unnecessary for either the manual or scheduled module command. On minimal Linux installations, install your distribution's `tzdata` package if `Europe/Paris` is unavailable.

To run tests (the hosted publication tests also require Git, included on GitHub's Ubuntu runners):

```sh
python3 -m unittest discover -s tests -v
```

Tests cover French/English matching, permanent/apprenticeship exclusions, C++ duties versus optional skills, Paris versus regional scope, deduplication, pagination, retry/error handling, availability evidence, atomic reports, stale-result preservation, and Paris DST scheduling. Test inputs are fixtures; their example jobs never appear in the real report.

## Configuration and coverage

Edit [config.toml](config.toml). The default `search.scope = "paris"` accepts explicit Paris offices, including hybrid roles attached to those offices. Bare remote roles and generic “Paris region” are insufficient. To expand the matching scope:

```toml
[search]
scope = "ile-de-france"
```

Region names, department postal codes and the configurable `idf_locations` commune list are recognized. Add unqualified town names used by your target sources to that list. Other locations are excluded. The employer feeds cover their whole boards. The `{location}` placeholder in public search URLs expands to Paris, France or Île-de-France, France according to scope; source URLs can also be edited. Boards may apply their own radius or interpret region names differently, so changing scope cannot guarantee region-wide discovery.

The supplied sources include Qube and Datadog Greenhouse feeds, Alice & Bob and Winamax Lever feeds, Meritis and Ubisoft SmartRecruiters feeds, Murex Workday, plus Optioncarriere, Hellowork and Welcome to the Jungle searches. Search adapters query both French and English terms. Employer feeds are scanned in full, except Workday's explicit bilingual internship/C++ keyword searches. See [docs/sources.md](docs/sources.md) for adapters, fields, adding employers and limitations. Sources are a maintained seed list; some board identifiers or HTML formats can change. Failures remain visible in the report.

Pagination continues to the source's end, without a chosen result cap. Repeated pages, inconsistent totals, unsupported pages, and inaccessible detail pages make a source incomplete. Public sites can restrict automated access or require JavaScript. The program does not bypass those restrictions or claim complete coverage of the job market.

The matching rules require an explicit internship contract, an in-scope location, and evidence of C++ engineering work. They exclude permanent employment, apprenticeships/alternance, marketing and optional C++-only skill mentions. Conservative rules can miss ambiguous relevant roles. Employer URLs and application links are preferred; stable URLs, requisition identifiers and normalized role identities deduplicate mirrors while preserving distinct known requisitions.

Confirmation requires positive evidence from an employer's job-specific candidate application route. HTTP 200, an indexed snippet or a public board's Apply label alone are insufficient. Expired deadlines, explicit closure notices and removed employer listings are excluded. Access errors and JavaScript-only forms remain Unverified. The program checks forms without submitting applications. Date and arrangement fields are reported only when supplied by a source; missing fields say “Not provided”.

## Run while your computer is off

Use [.github/workflows/internships.yml](.github/workflows/internships.yml) to run on GitHub-hosted Linux runners. It schedules the existing collector at **08:00 Europe/Paris**, including daylight saving changes, and commits `internship.md` plus `data/last-run.json` to the repository's default branch. Your computer can be off. The cloud copy is available on GitHub; update your local copy with `git pull` when you next connect.

Upload this project, including its `.github` directory, to a GitHub repository's default branch. Enable GitHub Actions, allow the workflow's `contents: write` permission, then open **Actions → Refresh internships → Run workflow** on that branch. Confirm that the hosted run finishes, the report's timestamp changes, and a report commit appears. See [docs/github-actions.md](docs/github-actions.md) for exact activation and verification steps, repository settings, and diagnostics. **This workflow is only prepared locally; no cloud schedule is active or verified yet.**

The hosted job saves partial results, and also saves the previous results with a stale banner after total collection failure before marking the run failed. Report/status/log artifacts are retained for seven days. Push permission failures or concurrent branch changes fail the job and leave the output in its artifact. Hosted runs use live HTTP and never the initial web-assisted archive. Use the default report/log paths in `config.toml`, or update the workflow and publisher's file paths together.

GitHub's schedule is best effort: jobs may be delayed or dropped, and it does not provide systemd's persistent catch-up behavior. Public repository schedules can be disabled after 60 days without repository activity. Actions usage and artifact storage depend on your GitHub plan. The workflow uses a GitHub-hosted runner; configuring a self-hosted runner on your computer would require it to remain online. See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). If precise timing and managed retries are essential, a hosted Linux VM running the existing systemd timer is another option.

## Local daily activation at 08:00 Europe/Paris

Run these commands in the normal host terminal, where systemd's user manager is available and the user configuration directory is writable:

```sh
cd /home/riccardo/agent
loginctl enable-linger "$(id -un)"
./scripts/install-schedule.sh
./scripts/verify-schedule.sh
systemctl --user list-timers --all internship-finder.timer
```

Enabling lingering may require the host administrator. The timer uses `OnCalendar=*-*-* 08:00:00 Europe/Paris`, so the IANA timezone follows daylight saving changes: 06:00 UTC in summer, 07:00 UTC in winter. It runs on this Linux machine independently of the agent session after successful installation. With `Persistent=true`, an offline machine gets **one catch-up run** when its user timer resumes, rather than replaying every missed day. It does not wake a sleeping machine. A failed collection retries individual HTTP requests, then waits until the next scheduled run unless manually started. See [docs/scheduling.md](docs/scheduling.md) for generated units, logs, disabling, and verification.

The installer regenerates units with absolute paths to this checkout, writes the user units, reloads systemd, enables the timer, and verifies its calendar, active/enabled state and user lingering. Moving the project requires reinstalling the timer. A verification failure exits nonzero and records the blocker; staged unit files alone do not establish an active schedule.

## Reliability and report interpretation

Successful and partial collections replace the report through a temporary file, flush/fsync and an atomic rename in the same directory. A nonblocking file lock prevents overlapping manual/scheduled runs. Temporary network failures use bounded retries with backoff; rate-limit Retry-After is respected up to 60 seconds per attempt. Logs rotate in `logs/internship-finder.log` (five backups); `data/last-run.json` stores run status and failed sources.

- Exit **0**: collection and verification completed for configured sources. An empty report says no matching internships were found **in those sources**.
- Exit **2**: incomplete search, including source or availability-check failures. Available results are published and unknown availability stays under **Unverified**.
- Exit **1**: total collection/startup failure. Collection failures preserve the previous report entries and original update timestamps, adding a prominent **STALE** banner with the failed-attempt timestamp and error. Invalid configuration fails before collection and does not alter the report; stderr identifies the startup failure. A first-ever collection failure explicitly says no successful results are available, not zero vacancies.
- Exit **3**: another collector holds the lock; no changes are made.

Useful diagnostics:

```sh
cat data/last-run.json
tail -n 80 logs/internship-finder.log
journalctl --user -u internship-finder.service --since today
./scripts/verify-schedule.sh
```

The initial web-assisted command was:

```sh
python3 -m internship_finder --config config.toml --web-snapshot data/bootstrap/search.json
```

That option still attempts the live sources, then imports a manually gathered archive with its actual retrieval timestamps and evidence. It refuses archives older than 24 hours. It is an explicit audit/recovery mechanism for this initial restricted environment; the scheduled runner never passes it, never calls the agent/web tool and never silently reuses archived results. Future network failures preserve the report as stale.
