"""Layer 2 — fuzzy string matching.

Several metrics are run and the best is taken, because they fail on different
things: Jaro-Winkler is strong on shared prefixes and typos but collapses on
word reordering, where token-sort recovers. "name_of_insured" vs
"insured_name" scores 58 on Jaro-Winkler and 89 on token-sort.

Fuzzy is deliberately *not* trusted on its own for anything important — see
guards.py. Its most dangerous errors (policy_start_date vs policy_end_date)
score higher than many of its correct matches.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from .models import Candidate, CanonicalField
from .normalize import normalize
from .schema import Schema


def _spaced(normalized: str) -> str:
    return normalized.replace("_", " ")


def score_pair(a_norm: str, b_norm: str) -> tuple[float, str]:
    """Best score across metrics, with the winning metric's name.

    `partial_ratio` and `token_set_ratio` are deliberately excluded. Both treat
    containment as identity, so "Title" vs "Title_Plan" scores 100 — merging
    exactly the pairs this system exists to keep apart. They also produce
    nonsense on short strings ("RM Code" vs "Relation" -> 100 on partial).
    """
    scores = {
        "jaro_winkler": JaroWinkler.normalized_similarity(a_norm, b_norm) * 100.0,
        "ratio": float(fuzz.ratio(a_norm, b_norm)),
        "token_sort": float(fuzz.token_sort_ratio(_spaced(a_norm), _spaced(b_norm))),
    }
    metric = max(scores, key=scores.get)
    return scores[metric], metric


def tokens_contained(a_norm: str, b_norm: str) -> bool:
    """True when one header's tokens are a strict subset of the other's.

    Containment is the structural signature of entity suffixing — `title` is
    contained in `title_plan`, `dob` in `nominee_dob`. Useless as a similarity
    score, but a strong signal that two fields are confusable.
    """
    ta = {t for t in a_norm.split("_") if t}
    tb = {t for t in b_norm.split("_") if t}
    if not ta or not tb or ta == tb:
        return False
    return ta < tb or tb < ta


def rank_candidates(
    header: str,
    schema: Schema,
    limit: int = 5,
    restrict_to: list[CanonicalField] | None = None,
) -> list[Candidate]:
    """Rank canonical fields against one partner header, best first.

    `restrict_to` narrows the search space once the entity is known, which is
    what prevents a nominee column from ever matching a proposer field.
    """
    h = normalize(header)
    pool = restrict_to if restrict_to is not None else schema.fields

    out: list[Candidate] = []
    for f in pool:
        # Long declaration fields would drag every partial score down; match on
        # the short_name when one is provided.
        target_text = f.short_name or f.name
        s, metric = score_pair(h, normalize(target_text))
        out.append(Candidate(field=f, score=s, metric=metric))

    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


def margin(candidates: list[Candidate]) -> float:
    """Gap between the top two candidates.

    A narrow margin means the header is ambiguous even when the top score is
    high — the signal that matters most for confusable schema fields.
    """
    if len(candidates) < 2:
        return 100.0
    return candidates[0].score - candidates[1].score
