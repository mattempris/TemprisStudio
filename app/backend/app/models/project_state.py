"""Pydantic project state models — see plan's Data & Persistence Design section.

ProjectMeta is the small, cheap-to-list object stored at
`client-<slug>/job-architecture/project.json`. ProjectState is the full
materialized state stored at `.../state/current.json`, overwritten after every
user-confirmed action.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class StageName(str, Enum):
    ingest = "ingest"
    strip = "strip"
    dedupe = "dedupe"
    normalize = "normalize"
    cluster = "cluster"
    name = "name"
    profile_je = "profile_je"
    skills = "skills"
    tasks = "tasks"
    matching = "matching"


class ProjectMeta(BaseModel):
    client_slug: str
    project_slug: str
    display_name: str
    # Document boilerplate. Step 1 strips company blurb and equality statements
    # OUT of the source job descriptions (they are noise for clustering); step 7
    # re-adds them at document level from here, so they are stated once per
    # project rather than inconsistently per input file.
    client_company_description: str | None = None
    diversity_statement: str | None = None
    accent_color: str = "#1d4ed8"
    created_at: datetime
    updated_at: datetime
    current_stage: StageName = StageName.ingest
    clustering_version: int = 1
    schema_version: int = 1


class RawInputFile(BaseModel):
    id: str
    filename: str
    blob_path: str
    kind: str  # "jd_file" | "hris"
    uploaded_at: datetime
    content_hash: str


class ColumnMapping(BaseModel):
    job_title_col: str | None = None
    job_description_col: str | None = None
    job_level_col: str | None = None
    headcount_col: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    reasoning: dict[str, str] = Field(default_factory=dict)
    user_confirmed: bool = False


class JobRecordRaw(BaseModel):
    id: str
    source_file_id: str
    source_row_index: int | None = None
    job_title: str
    raw_text: str
    level_raw: str | None = None
    headcount: int | None = None


class JobRecordStripped(BaseModel):
    id: str
    stripped_text: str
    removed_sections: list[str] = Field(default_factory=list)
    model: str
    generated_at: datetime


class DedupeGroup(BaseModel):
    group_id: str
    member_ids: list[str]
    representative_id: str
    avg_similarity: float
    user_confirmed: bool = False


class NormalizedProfile(BaseModel):
    id: str  # == dedupe group_id
    source_record_ids: list[str]
    purpose_statement: str
    key_tasks: list[str]
    management_line: str | None = None
    budget_responsibility: str | None = None
    generated_at: datetime


class ItemAssignmentRecord(BaseModel):
    """Persisted form of clustering.engine.ItemAssignment, keyed by item id rather
    than array index so it survives serialization/reload."""

    item_id: str
    backbone_profile_id: int
    backbone_category_id: int
    backbone_family_id: int
    final_profile_id: int
    final_category_id: int
    final_family_id: int
    stability_score: float | None = None
    routed_by_llm: bool = False
    route_confidence: float | None = None
    secondary_profile_id: int | None = None
    secondary_confidence: float | None = None
    self_consistency: dict | None = None


class ClusteringState(BaseModel):
    embedding_model: str = "jobQWEN"
    linkage_blob_path: str
    embedding_index_blob_path: str
    k_profiles: int
    k_categories: int
    k_families: int
    assignments: list[ItemAssignmentRecord] = Field(default_factory=list)
    profile_names: dict[int, str] = Field(default_factory=dict)
    category_names: dict[int, str] = Field(default_factory=dict)
    family_names: dict[int, str] = Field(default_factory=dict)
    gate: float = 0.58
    computed_at: datetime | None = None
    version: int = 1


class TierMemberRecord(BaseModel):
    """One item's outcome at one tier of the hierarchy.

    `item_id` is a raw record id at the profile tier and a synthetic
    "profile:<n>" / "category:<n>" at the coarser tiers, because what gets
    clustered there is the previous tier's clusters.
    """

    item_id: str
    backbone_cluster_id: int
    final_cluster_id: int
    stability_score: float | None = None
    routed_by_llm: bool = False
    route_confidence: float | None = None
    secondary_cluster_id: int | None = None
    secondary_confidence: float | None = None
    self_consistency: dict | None = None
    # A person moved this to a different cluster after the run. Recorded alongside
    # the backbone and routing decisions rather than replacing them, so a placement
    # that was overridden by hand can still be told apart from one the model chose.
    moved_by_user: bool = False


class TierState(BaseModel):
    """A confirmed tier: the counts chosen, the gate used, the names, and the full
    audit trail of what the model moved.

    Each tier is confirmed separately (instructions.txt steps 5-6, split so profiles,
    categories and families are each reviewed on their own), so each carries its own
    k and gate rather than sharing one setting across the hierarchy.
    """

    tier: str  # "profile" | "category" | "family"
    k: int
    gate: float
    embedding_model: str = ""
    names: dict[int, str] = Field(default_factory=dict)
    members: list[TierMemberRecord] = Field(default_factory=list)
    # A few member texts per cluster, kept so the next tier can describe its items
    # without reloading and re-summarising the tier below.
    exemplars: dict[int, list[str]] = Field(default_factory=dict)
    centroids_blob_path: str = ""
    n_routed: int = 0
    n_moved: int = 0
    computed_at: datetime | None = None


class JESubdomainConfig(BaseModel):
    name: str
    weight: float
    rubric: list[str] = Field(default_factory=list)  # 5 behavioral descriptors, level 1-5


class JEDomainConfig(BaseModel):
    name: str
    weight: float
    subdomains: list[JESubdomainConfig] = Field(default_factory=list)


class LevelBand(BaseModel):
    name: str
    min_score: float
    max_score: float


class JEFrameworkConfig(BaseModel):
    domains: list[JEDomainConfig] = Field(default_factory=list)
    level_bands: list[LevelBand] = Field(default_factory=list)


class ProfileSectionConfig(BaseModel):
    """One section of the user-defined job profile template (step 7).

    `key` must be one of services/job_profile/template_config.py's catalogue —
    each key carries the shape (prose / list / chips / inline) that the HTML, PDF
    and DocX renderers all know how to lay out.
    """

    key: str
    heading: str
    include: bool = True
    guidance: str = ""


class JobProfileTemplateConfig(BaseModel):
    """Empty `sections` means the project has never customised the template, so
    the shipped default catalogue applies."""

    sections: list[ProfileSectionConfig] = Field(default_factory=list)


class JobProfileDoc(BaseModel):
    profile_key: str
    profile_cluster_id: int
    clustering_version: int
    title: str
    content: dict
    html: str
    generated_at: datetime
    export_paths: dict[str, str] = Field(default_factory=dict)
    stale: bool = False


class JEEvaluationResult(BaseModel):
    profile_key: str
    clustering_version: int
    framework_version_hash: str
    personas: dict[str, dict]
    aggregate_score: float
    level_name: str
    computed_at: datetime
    stale: bool = False


class InferredSkillRecord(BaseModel):
    """A skill as inferred from one job profile (step 8), before clustering."""

    id: str
    name: str
    description: str
    kind: str  # "technical" | "non-technical"
    source_profile_key: str


class ProficiencyLevelConfig(BaseModel):
    name: str
    ordinal: int
    criteria: str
    typical_autonomy: str = ""


class ProficiencyTemplateConfig(BaseModel):
    levels: list[ProficiencyLevelConfig] = Field(default_factory=list)


class ClusterProficiencyRecord(BaseModel):
    cluster_id: int
    cluster_name: str
    definitions: dict[str, str] = Field(default_factory=dict)  # level name -> definition


class ProfileSkillRequirementRecord(BaseModel):
    """Output of the deterministic rollup plus the LLM level assignment (step 9)."""

    profile_key: str
    cluster_id: int
    cluster_name: str
    contributing_skills: list[str] = Field(default_factory=list)
    assigned_level: str | None = None
    rationale: str | None = None


class SkillsState(BaseModel):
    """Everything from instructions.txt steps 8-9."""

    inferred: list[InferredSkillRecord] = Field(default_factory=list)
    # Per-tier records, confirmed one tier at a time by the same engine the job
    # hierarchy uses (with skillQWEN embeddings). `clustering` is the denormalised
    # view derived from them once all three exist — everything downstream reads it.
    clustering_tiers: dict[str, TierState] = Field(default_factory=dict)
    clustering: ClusteringState | None = None
    proficiency_template: ProficiencyTemplateConfig = Field(default_factory=ProficiencyTemplateConfig)
    cluster_proficiencies: list[ClusterProficiencyRecord] = Field(default_factory=list)
    profile_requirements: list[ProfileSkillRequirementRecord] = Field(default_factory=list)
    audit: dict = Field(default_factory=dict)  # inference audit counts, for transparency


class InferredTaskRecord(BaseModel):
    """A task as inferred from one job profile (step 10), before clustering.
    `proportion` is that task's share of the job holder's time; the proportions
    for one profile_key are guaranteed to sum to 100."""

    id: str
    name: str
    description: str
    proportion: float
    source_profile_key: str


class TasksState(BaseModel):
    inferred: list[InferredTaskRecord] = Field(default_factory=list)
    clustering_tiers: dict[str, TierState] = Field(default_factory=dict)
    clustering: ClusteringState | None = None
    audit: dict = Field(default_factory=dict)


class TaskActionRecord(BaseModel):
    """One action inside a task cluster — Workforce Studio step 3.

    A task cluster says *what kind of work* this is; its actions say what a person
    actually does within it, which is the level AI opportunity is real at. "Handling
    Customer Complaints" is not automatable or not; the acknowledgement letter inside
    it largely is, and the judgement call on redress is not.

    `pct_of_task` values for one cluster are guaranteed by code to sum to 100.
    """

    id: str
    task_cluster_id: int
    name: str
    definition: str
    pct_of_task: float
    # What AI can do unattended, and how much faster a person is with AI help. Two
    # separate axes because they genuinely diverge: contract review automates badly
    # (someone must own a missed clause) and augments well (the reading disappears).
    # One score would rank step 5's prompt list by how replaceable each task is.
    automation_pct: float
    augmentation_pct: float


class TaskOpportunityRecord(BaseModel):
    """A task cluster's rolled-up opportunity. Derived from its actions in code,
    never asked of the model — same precedent as JE aggregation and dedupe."""

    task_cluster_id: int
    cluster_name: str
    automation_pct: float
    augmentation_pct: float
    n_actions: int
    # What the model's percentages summed to before normalisation, and whether any
    # score had to be pulled back into range. Kept so a run's calibration quality is
    # inspectable rather than invisible.
    raw_pct_sum: float = 100.0
    clamped: bool = False
    computed_at: datetime | None = None


class WorkforceState(BaseModel):
    """Workforce Studio's own state. Phase B populates actions and opportunity;
    processes, agents, skills guidance and future roles arrive with their phases."""

    actions: list[TaskActionRecord] = Field(default_factory=list)
    opportunity: list[TaskOpportunityRecord] = Field(default_factory=list)
    # Hours in a full-time week, used only to express released capacity in hours.
    # Configurable because a 35-hour and a 40-hour week give materially different
    # numbers and neither is a safe silent default.
    hours_per_fte_week: float = 37.5
    graph_version: int = 1
    audit: dict = Field(default_factory=dict)


class TaxonomyCandidateRecord(BaseModel):
    """One shortlisted specialization the reranker chose from. Kept in full so a
    challenged match can be re-argued against what was actually on the table."""

    code: str
    title: str
    family_title: str
    sub_family_title: str
    cosine: float


class TaxonomyMatchRecord(BaseModel):
    """A job profile's placement in the 3rd-party taxonomy (step 11).

    `matched=False` with `review_reasons=["no_match"]` is a real, useful outcome —
    the taxonomy has no bucket for this role — and is stored rather than dropped.
    """

    profile_key: str
    profile_title: str
    matched: bool
    spec_code: str | None = None
    spec_title: str | None = None
    family_title: str | None = None
    sub_family_title: str | None = None
    cosine: float | None = None
    confidence: float = 0.0
    rationale: str = ""
    runner_up_code: str | None = None
    runner_up_title: str | None = None
    level_code: str | None = None
    level_title: str | None = None
    level_stream: str | None = None
    level_confidence: float = 0.0
    level_rationale: str = ""
    shortlist: list[TaxonomyCandidateRecord] = Field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    # Set when a user overrides the machine match, so the two never get confused.
    overridden_by_user: bool = False


class MatchingState(BaseModel):
    industries: list[str] = Field(default_factory=list)
    shortlist_size: int = 12
    matches: list[TaxonomyMatchRecord] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    computed_at: datetime | None = None


class ProjectState(BaseModel):
    meta: ProjectMeta
    inputs: list[RawInputFile] = Field(default_factory=list)
    column_mapping: ColumnMapping | None = None
    raw_records: list[JobRecordRaw] = Field(default_factory=list)
    stripped_records: list[JobRecordStripped] = Field(default_factory=list)
    dedupe_threshold: float | None = None
    dedupe_groups: list[DedupeGroup] = Field(default_factory=list)
    normalized_profiles: list[NormalizedProfile] = Field(default_factory=list)
    # Per-tier records, keyed "profile" | "category" | "family". This is the
    # authoritative account of the hierarchy; `clustering` below is a denormalised
    # view rebuilt from these so that everything downstream — job profiles, the
    # overview, exports, the headcount rollups — keeps reading one flat structure.
    clustering_tiers: dict[str, TierState] = Field(default_factory=dict)
    clustering: ClusteringState | None = None
    je_framework: JEFrameworkConfig = Field(default_factory=JEFrameworkConfig)
    profile_template: JobProfileTemplateConfig = Field(default_factory=JobProfileTemplateConfig)
    job_profiles: list[JobProfileDoc] = Field(default_factory=list)
    je_results: list[JEEvaluationResult] = Field(default_factory=list)
    skills: SkillsState = Field(default_factory=SkillsState)
    tasks: TasksState = Field(default_factory=TasksState)
    matching: MatchingState = Field(default_factory=MatchingState)
    # Workforce Studio. Reads everything above; nothing above reads it.
    workforce: WorkforceState = Field(default_factory=WorkforceState)
