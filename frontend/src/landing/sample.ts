import raw from "./sample.json";

/**
 * One real offline pipeline run on the library spec. Generated and drift-checked by
 * tests/test_landing_sample.py, so every number and file on the landing page is what the
 * code produces today.
 */
export interface Sample {
  spec: string;
  provider: { name: string; model: string };
  stages: { name: string; label: string; logs: string[]; summary: string }[];
  requirements: {
    project_name: string;
    functional_requirements: string[];
    user_stories: string[];
    entities: { name: string; fields: [string, string, boolean][] }[];
  };
  architecture: {
    style: string;
    components: { name: string; technology: string }[];
    endpoints: [string, string][];
    decisions: string[];
    deployment: { cloud_target: string; database: string; replicas: number; container_port: number };
  };
  files: { path: string; size: number; stage: string; language: string }[];
  showcase: { path: string; language: string; content: string }[];
  validation: { files_checked: number; errors: number; warnings: number; passed: boolean };
  review: {
    score: number;
    summary: string;
    findings: { severity: string; file: string | null; message: string; suggestion: string }[];
  };
}

export const sample = raw as Sample;

export const filesByStage = (stage: string) => sample.files.filter((file) => file.stage === stage);

export const stageOf = (name: string) => {
  const stage = sample.stages.find((s) => s.name === name);
  if (!stage) throw new Error(`sample has no stage ${name}`);
  return stage;
};
