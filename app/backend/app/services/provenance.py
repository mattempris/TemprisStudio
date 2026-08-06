"""Where an anchor role's content actually came from.

Skills and tasks are inferred from the anchor role *document*, which sits two model calls away
from the job description that was uploaded:

    uploaded JD
      -> stripped        (extractive; boilerplate removed)
      -> normalised      (a model call: one purpose sentence + 5-10 key task phrases)
      -> anchor role     (clustering; several normalised jobs merged into one)
      -> role document   (a model call: written from the normalised members)
      -> skills / tasks  (a model call reading that document)

Each hop is a compression. The normalise step alone reduces a job description that lists
twenty-five responsibilities to about eight short phrases, and the document is then written from
those phrases rather than from the source. For a workforce assembled from thin HRIS rows that is
the point — the compression is what makes hundreds of inconsistent records comparable.

For a project whose inputs are already comprehensive, distinct, well-written job profiles it is
pure loss. So where an anchor role turns out to stand for exactly one uploaded record, this
module recovers that record's text and the inference steps read it directly.

**The test is on the data, never on a flag.** It is tempting to check `skipped_steps` for
"dedupe" and "cluster", but that answers a different question. A real clustering run routinely
produces singleton clusters, and those are 1:1 too and deserve the same treatment. Equally, a
skipped dedupe followed by real clustering produces merged anchor roles that are not. What
matters is whether the chain from this anchor role back to the upload passes through exactly one
record at every step, and that is a property of the data.
"""
from __future__ import annotations

from app.models.project_state import ProjectState


def single_source_text(state: ProjectState) -> dict[str, str]:
    """profile_key -> the stripped text of its one source record, where there is exactly one.

    An anchor role qualifies only when every link in the chain is one-to-one: the profile
    cluster holds a single normalised job, and that job's dedupe group holds a single uploaded
    record. Anything merged is excluded, because there is no single source description to pass
    and concatenating several would re-create the synthesis the document already does — worse,
    without the document's structure.

    The *stripped* text rather than the raw text. Stripping is extractive, so it removes company
    boilerplate, benefits and recruitment logistics without touching the substance — which is
    exactly the noise that would otherwise be read as skills and tasks. When the strip step was
    skipped, `stripped_text` is the raw text verbatim, so this works either way with no branch.

    Returns an empty mapping when the hierarchy has not been clustered yet, since profile keys
    do not exist before then.
    """
    c = state.clustering
    if c is None:
        return {}

    # profile cluster id -> the normalised jobs in it
    members: dict[int, list[str]] = {}
    for a in c.assignments:
        members.setdefault(a.final_profile_id, []).append(a.item_id)

    group_members = {g.group_id: g.member_ids for g in state.dedupe_groups}
    stripped = {r.id: r.stripped_text for r in state.stripped_records if r.stripped_text.strip()}

    out: dict[str, str] = {}
    for doc in state.job_profiles:
        if doc.stale:
            continue
        norm_ids = members.get(doc.profile_cluster_id, [])
        if len(norm_ids) != 1:
            continue  # the anchor role merges several jobs
        # A normalised profile's id IS its dedupe group id.
        records = group_members.get(norm_ids[0], [norm_ids[0]])
        if len(records) != 1:
            continue  # the job itself was a merge of several uploaded records
        text = stripped.get(records[0])
        if text:
            out[doc.profile_key] = text
    return out


def coverage(state: ProjectState) -> tuple[int, int]:
    """(anchor roles with a single source record, anchor roles in total).

    Reported so the UI can say which inputs the inference actually read. A step that silently
    reads different things for different roles is one whose output nobody can account for.
    """
    live = [d for d in state.job_profiles if not d.stale]
    return len(single_source_text(state)), len(live)
