/** The checks in src/autoswe/pipeline/validation.py, grouped as the reviewer runs them. */
export const GROUPS: { title: string; items: string[] }[] = [
  {
    title: "Parsed, not pattern-matched",
    items: ["Python with ast", "YAML with PyYAML", "Terraform with python-hcl2", "JSON and TOML"],
  },
  {
    title: "Kubernetes",
    items: [
      "Liveness and readiness probes",
      "Resource limits",
      "Non-root containers",
      "No privileged containers",
    ],
  },
  {
    title: "Dockerfile",
    items: ["Pinned base images", "Non-root final user", "A HEALTHCHECK"],
  },
  {
    title: "GitHub Actions",
    items: ["A trigger and jobs with steps", "runs-on for every job", "Version-pinned actions"],
  },
  {
    title: "Safety",
    items: ["Secret scanner", "Path-safety and size limits", "Unsafe paths rejected before storage"],
  },
  {
    title: "Completeness",
    items: [
      "README, manifest, Dockerfile, tests, CI",
      "Kubernetes and Terraform present",
      "Every designed endpoint found in the code",
    ],
  },
];

/** How many checks the page lists (the Validation section shows each one). */
export const CHECK_COUNT = GROUPS.reduce((sum, group) => sum + group.items.length, 0);
