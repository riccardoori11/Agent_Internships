"""Conservative, explainable contract, location and C++ matching."""
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import RawJob


def normalize(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).lower()


def plain(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


CPP = re.compile(r"(?<![a-z])c\s*\+\s*\+|\bcpp\b", re.I)
INTERN = re.compile(r"\b(internship|intern|internships|stagiaire|stage|stages|traineeship)\b", re.I)
EXCLUDED = re.compile(r"\b(cdi|permanent|apprentice\w*|apprentissage|alternance|alternant\w*|work.study)\b", re.I)
TECH = re.compile(r"develop|design|implement|build|program|software|engineer|ingenieur|logiciel|concev|conce[vp]|cod[ei]|optimis|optimiz|maint[ae]|component|composant|algorithm|simulat|compil|system|robot|r&d", re.I)
OPTIONAL = re.compile(r"nice.to.have|optional|bonus|a plus|un plus|ideally|ou python|or python|familiar|connaissance|knowledge|\bskills?\b|maitrise|proficien", re.I)
SOFT_OPTIONAL = re.compile(r"nice.to.have|optional|bonus|a plus|un plus|ideally|ou python|or python", re.I)


def location_matches(location: str, scope: str, extra_locations=()) -> bool:
    text = normalize(location)
    if re.search(r"\b(texas|ontario|tennessee|kentucky|arkansas|united states|canada|usa|tx)\b", text):
        return False
    # The city itself, including arrondissement and postal-code addresses. Paris-Saclay
    # and generic 'Paris region' are deliberately outside the city scope.
    paris = bool(re.search(r"\b75\d{3}\b", text) or re.search(r"\bparis\b(?![ -](?:saclay|region))", text))
    if re.search(r"grand paris|greater paris|region parisienne|paris area|paris metropolitan", text):
        paris = False
    if scope == "paris":
        return paris
    if scope != "ile-de-france":
        raise ValueError("search.scope must be 'paris' or 'ile-de-france'")
    return paris or bool(re.search(r"ile.de.france|paris.saclay|paris region|region parisienne|grand paris|greater paris|paris area|\b(?:77|78|91|92|93|94|95)\d{3}\b", text)) or any(
        re.search(r"(?<!\w)" + re.escape(normalize(town)) + r"(?!\w)", text) for town in extra_locations
    )


def cpp_evidence(job: RawJob) -> str:
    description = plain(job.description)
    sentences = re.split(r"(?<=[.!?;])\s+|[\r\n]+", html.unescape(re.sub(r"</(?:p|li|div)>|<br\s*/?>", "\n", job.description, flags=re.I)))
    for sentence in sentences:
        sentence = plain(sentence)
        normalized = normalize(sentence)
        if CPP.search(normalized) and TECH.search(normalized) and not OPTIONAL.search(normalized):
            return sentence[:500]
    optional_cpp_only = any(CPP.search(plain(s)) and SOFT_OPTIONAL.search(normalize(plain(s))) for s in sentences)
    if not optional_cpp_only and CPP.search(job.title) and TECH.search(normalize(job.title)) and re.search(r"develop|design|implement|build|maintain|optimis|optimiz|concevoir", normalize(description)):
        duty = next((plain(s) for s in sentences if re.search(r"develop|design|implement|build|maintain|optimis|optimiz|concevoir", normalize(plain(s))) and not OPTIONAL.search(normalize(plain(s)))), "")
        return (job.title + (" — " + duty if duty else ""))[:500]
    return ""


def match_job(job: RawJob, scope="paris", extra_locations=()) -> tuple[bool, str]:
    contract = normalize(job.contract_type)
    title = normalize(job.title)
    if EXCLUDED.search(contract + " " + title):
        return False, "Excluded permanent/apprenticeship contract"
    labeled_contract = re.search(r"(?:contract type|type (?:de )?(?:contrat|emploi))\s*:?\s*([^.;\n]{1,70})", normalize(plain(job.description)))
    if labeled_contract and EXCLUDED.search(labeled_contract[1]):
        return False, "Explicit excluded contract in description"
    explicit_contract = re.search(r"(?:this (?:is|role is|position is)|il s'agit d['’](?:un|une)|(?:nous (?:proposons|offrons)|vous recherchez) (?:un|une)|contrat (?:en |d['’])?)[^.;]{0,45}\b(?:apprenticeship|alternance|apprentissage|permanent)\b", normalize(plain(job.description)))
    if explicit_contract:
        return False, "Explicit excluded contract in description"
    if not INTERN.search(contract + " " + title):
        # Accept an explicit contract in a description, not an incidental reference
        # such as 'mentor interns' on a permanent engineering vacancy.
        if not re.search(r"(?:type (?:de )?(?:contrat|emploi)|contract type)\s*:?\s*(?:stage|internship)\b", normalize(plain(job.description))):
            return False, "No explicit internship contract"
    if not location_matches(job.location, scope, extra_locations):
        return False, "Outside configured location scope"
    evidence = cpp_evidence(job)
    if not evidence:
        return False, "No evidence of meaningful C++ work"
    job.metadata["cpp_evidence"] = evidence
    return True, evidence


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k.lower() not in {"source", "ref", "referrer", "trackingid", "lever-source"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def _text_key(value: str) -> str:
    return re.sub(r"[^a-z0-9+]", "", normalize(value))


def _rank(job: RawJob) -> tuple:
    aggregator = any(domain in urlsplit(job.url).netloc for domain in ("indeed.", "linkedin.", "welcometothejungle.", "hellowork.", "optioncarriere.", "offres.tech"))
    return (bool(job.metadata.get("employer_original", job.metadata.get("employer_source", not aggregator))), job.status == "open", len(job.description))


def deduplicate(jobs: list[RawJob]) -> list[RawJob]:
    def related(job, other):
        def links(item):
            return {canonical_url(u) for u in (item.url, item.apply_url) if u and urlsplit(u).path.strip("/") not in {"", "careers", "jobs", "apply"}}
        ids = tuple(str(item.metadata.get("requisition_id") or item.metadata.get("identifier") or "").strip() for item in (job, other))
        company = _text_key(job.company) == _text_key(other.company)
        same_location = _text_key(job.location) == _text_key(other.location) or (location_matches(job.location, "paris") and location_matches(other.location, "paris"))
        identity = company and _text_key(job.title) == _text_key(other.title) and same_location
        return bool(links(job) & links(other)) or bool(company and all(ids) and ids[0] == ids[1]) or (identity and not (all(ids) and ids[0] != ids[1]))

    groups: list[list[RawJob]] = []
    for job in jobs:
        related_groups = [group for group in groups if any(related(job, other) for other in group)]
        merged = [job]
        for group in related_groups:
            merged.extend(group)
            groups.remove(group)
        groups.append(merged)
    result = []
    for group in groups:
        chosen = max(group, key=_rank)
        chosen.metadata["discovered_on"] = sorted({j.source for j in group if j.source})
        chosen.metadata["duplicate_urls"] = sorted({j.url for j in group if j.url != chosen.url})
        result.append(chosen)
    return sorted(result, key=lambda j: (_text_key(j.company), _text_key(j.title), j.url))
