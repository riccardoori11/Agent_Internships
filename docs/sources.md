# Collection sources and extension points

The daily collector uses ordinary HTTPS requests. It does not depend on the agent's web search tools, private credentials, or a paid search API. Employer boards and public job search pages are configured in `config.toml`; the scope is finite and therefore cannot guarantee every vacancy in Paris.

Supported `[[sources]]` entries:

| `kind` | Required configuration | Exhaustion rule |
| --- | --- | --- |
| `greenhouse` | `name`, `board`, `company` | Full `jobs?content=true` response; check advertised total |
| `lever` | `name`, `board`, `company`; optional `host` for EU API | `skip`/`limit` pages through an empty page |
| `ashby` | `name`, `board`, `company` | Complete public posting API response |
| `smartrecruiters` | `name`, `board`, `company`; optional `country` | `offset`/`limit` through `totalFound`; fetch internship details |
| `workday` | `name`, `host`, `tenant`, `board`, `company`; optional `queries` | Each query through advertised total, followed by full posting details |
| `html_search` | `name`, `url` with optional `{query}` and `{location}`, `queries`; optional `job_link_pattern`, `empty_pattern`, `employer` | Follow explicit next links; retrieve JSON-LD JobPosting detail pages |
| `jobpage` | `name`, `url`; optional `company`, `employer` | Parse every JSON-LD JobPosting on that single configured page |

Page sizes are transport sizes, never a result limit. Repeated pagination, an advertised total that cannot be exhausted, invalid responses and unsupported page structures mark a source incomplete. HTML pagination recognises next links, next page accessibility labels and numbered `page`/`p`/`pageNumber` links. A page counter advertising more pages with no recognisable next link fails visibly. Enabled JavaScript Load more/Next/Voir plus controls without a usable next link, or an explicit job count larger than distinct postings collected across the query's pages, also mark collection incomplete. HTML adapters require structured descriptions; they deliberately fail when a site serves only JavaScript or a bot challenge, rather than reporting a misleading empty result. Hidden translation strings such as “no results” do not establish an empty search. Public sites may change markup, restrict requests or require credentials. These failures appear in the report and logs.

Sources run concurrently (`network.source_workers`, default 4), while each individual source follows its own pages sequentially. Start dates and durations are copied from structured fields or explicit French/English wording; source snippets are retained in `metadata.field_evidence`. A cohort year alone does not establish a start date. Explicit Apply/Postuler links to recognised employer ATS hosts are preferred for application, then independently verified. A source's update timestamp is never relabelled as a posting date.

French and English queries are explicit configuration values, for example `queries = ["stage C++", "C++ internship"]`. URL `{location}` expands to URL-encoded `Paris, France` by default, or `Île-de-France, France` when `search.scope = "ile-de-france"`. This broadens public search discovery as well as the final location filter. Source configuration is copied before expansion. Full-board APIs scan every posting and matching subsequently recognises both languages. Workday's public frontend endpoint is not a versioned public API and may change. A configured single job page is supplementary discovery only: it cannot discover newly created requisitions by itself.

The initial environment may permit agent web research while preventing Python network access. Explicit snapshot runs are useful for auditing previously captured pages, but never count as a fresh daily HTTP search. A snapshot manifest records exact URL, capture time, local body filename and provenance. Missing snapshot URLs fail; no silent network fallback or snapshot reuse occurs during normal runs. Do not synthesise a JSON-LD response and label it as the employer's raw response.

Public API documentation: [Greenhouse Job Board](https://docs.greenhouse.io/job-board.html), [Lever Postings](https://github.com/lever/postings-api), [SmartRecruiters Posting](https://developers.smartrecruiters.com/docs/posting-api), [Ashby Job Postings](https://developers.ashbyhq.com/docs/public-job-posting-api). These document feed semantics; availability is checked separately on the original employer posting/application page.
