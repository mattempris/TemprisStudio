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
    client_company_description: str | None = None
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
    # Same shape as ClusteringState but over skills, produced by the same engine
    # with skillQWEN embeddings.
    clustering: ClusteringState | None = None
    proficiency_template: ProficiencyTemplateConfig = Field(default_factory=ProficiencyTemplateConfig)
    cluster_proficiencies: list[ClusterProficiencyRecord] = Field(default_factory=list)
    profile_requirements: list[ProfileSkillRequirementRecord] = Field(default_factory=list)
    audit: dict = Field(default_factory=dict)  # inference audit counts, for transparency


class ProjectState(BaseModel):
    meta: ProjectMeta
    inputs: list[RawInputFile] = Field(default_factory=list)
    column_mapping: ColumnMapping | None = None
    raw_records: list[JobRecordRaw] = Field(default_factory=list)
    stripped_records: list[JobRecordStripped] = Field(default_factory=list)
    dedupe_threshold: float | None = None
    dedupe_groups: list[DedupeGroup] = Field(default_factory=list)
    normalized_profiles: list[NormalizedProfile] = Field(default_factory=list)
    clustering: ClusteringState | None = None
    je_framework: JEFrameworkConfig = Field(default_factory=JEFrameworkConfig)
    job_profiles: list[JobProfileDoc] = Field(default_factory=list)
    je_results: list[JEEvaluationResult] = Field(default_factory=list)
    skills: SkillsState = Field(default_factory=SkillsState)
