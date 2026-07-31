export interface ProjectMeta {
  client_slug: string;
  project_slug: string;
  display_name: string;
  client_company_description: string | null;
  accent_color: string;
  created_at: string;
  updated_at: string;
  current_stage: string;
  clustering_version: number;
  schema_version: number;
}
