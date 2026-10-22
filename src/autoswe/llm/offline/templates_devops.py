"""Delivery artifacts the offline DevOps agent produces: Dockerfile, CI, Kubernetes, Terraform."""

from __future__ import annotations

from textwrap import dedent

from autoswe.pipeline.artifacts import ArchitectureSpec, GeneratedFile


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


def render_dockerfile(port: int) -> str:
    return _src(f"""
        # syntax=docker/dockerfile:1.7
        FROM python:3.12-slim AS build
        ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
        WORKDIR /src
        RUN python -m venv /opt/venv
        ENV PATH=/opt/venv/bin:$PATH
        COPY pyproject.toml README.md ./
        COPY app ./app
        RUN pip install .

        FROM python:3.12-slim AS runtime
        ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
        RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
        COPY --from=build /opt/venv /opt/venv
        WORKDIR /home/app
        USER 10001
        EXPOSE {port}
        HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \\
          CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/healthz', timeout=3)"]
        CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "{port}"]
    """)


DOCKERIGNORE = _src("""
    .git
    .github
    .venv
    __pycache__/
    *.pyc
    *.db
    .pytest_cache/
    .ruff_cache/
    deploy/
    tests/
""")


def render_ci(name: str) -> str:
    return _src(f"""
        name: CI

        on:
          push:
            branches: [main]
          pull_request:

        permissions:
          contents: read

        jobs:
          test:
            runs-on: ubuntu-latest
            steps:
              - uses: actions/checkout@v4
              - uses: actions/setup-python@v5
                with:
                  python-version: "3.12"
                  cache: pip
              - name: Install
                run: pip install -e . pytest httpx ruff
              - name: Lint
                run: ruff check .
              - name: Test
                run: pytest -q

          image:
            runs-on: ubuntu-latest
            needs: test
            steps:
              - uses: actions/checkout@v4
              - uses: docker/setup-buildx-action@v3
              - name: Build image
                uses: docker/build-push-action@v6
                with:
                  context: .
                  push: false
                  tags: {name}:${{{{ github.sha }}}}
    """)


def render_k8s_deployment(name: str, port: int, replicas: int) -> str:
    return _src(f"""
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: {name}
          labels:
            app.kubernetes.io/name: {name}
        spec:
          replicas: {replicas}
          selector:
            matchLabels:
              app.kubernetes.io/name: {name}
          template:
            metadata:
              labels:
                app.kubernetes.io/name: {name}
            spec:
              securityContext:
                runAsNonRoot: true
                runAsUser: 10001
                seccompProfile:
                  type: RuntimeDefault
              containers:
                - name: api
                  image: {name}:latest
                  imagePullPolicy: IfNotPresent
                  ports:
                    - name: http
                      containerPort: {port}
                  env:
                    - name: DATABASE_URL
                      valueFrom:
                        secretKeyRef:
                          name: {name}-database
                          key: url
                          optional: true
                  readinessProbe:
                    httpGet:
                      path: /healthz
                      port: http
                    periodSeconds: 10
                  livenessProbe:
                    httpGet:
                      path: /healthz
                      port: http
                    initialDelaySeconds: 10
                    periodSeconds: 20
                  resources:
                    requests:
                      cpu: 100m
                      memory: 128Mi
                    limits:
                      cpu: 500m
                      memory: 256Mi
                  securityContext:
                    allowPrivilegeEscalation: false
                    capabilities:
                      drop: [ALL]
    """)


def render_k8s_service(name: str, port: int) -> str:
    return _src(f"""
        apiVersion: v1
        kind: Service
        metadata:
          name: {name}
          labels:
            app.kubernetes.io/name: {name}
        spec:
          selector:
            app.kubernetes.io/name: {name}
          ports:
            - name: http
              port: 80
              targetPort: http
    """)


def render_kustomization(name: str) -> str:
    return _src(f"""
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        labels:
          - pairs:
              app.kubernetes.io/part-of: {name}
        resources:
          - deployment.yaml
          - service.yaml
    """)


TF_VERSIONS = _src("""
    terraform {
      required_version = ">= 1.6"
      required_providers {
        aws = {
          source  = "hashicorp/aws"
          version = "~> 5.0"
        }
      }
    }

    provider "aws" {
      region = var.region
    }
""")


def render_tf_variables(name: str, port: int, replicas: int) -> str:
    return _src(f"""
        variable "region" {{
          description = "AWS region to deploy into"
          type        = string
          default     = "us-east-1"
        }}

        variable "name" {{
          description = "Name prefix for every resource"
          type        = string
          default     = "{name}"
        }}

        variable "image_tag" {{
          description = "Immutable image tag in the ECR repository to run (e.g. a git SHA)"
          type        = string
        }}

        variable "vpc_id" {{
          description = "VPC the service runs in"
          type        = string
        }}

        variable "subnet_ids" {{
          description = "Private subnets for the Fargate tasks"
          type        = list(string)
        }}

        variable "container_port" {{
          description = "Port the container listens on"
          type        = number
          default     = {port}
        }}

        variable "desired_count" {{
          description = "Number of running tasks"
          type        = number
          default     = {replicas}
        }}

        variable "database_url_secret_arn" {{
          description = "Optional Secrets Manager ARN holding DATABASE_URL"
          type        = string
          default     = ""
        }}
    """)


TF_MAIN = _src("""
    locals {
      secrets = var.database_url_secret_arn == "" ? [] : [
        { name = "DATABASE_URL", valueFrom = var.database_url_secret_arn }
      ]
    }

    resource "aws_ecr_repository" "app" {
      name                 = var.name
      image_tag_mutability = "IMMUTABLE"

      image_scanning_configuration {
        scan_on_push = true
      }
    }

    resource "aws_cloudwatch_log_group" "app" {
      name              = "/ecs/${var.name}"
      retention_in_days = 30
    }

    resource "aws_ecs_cluster" "this" {
      name = var.name

      setting {
        name  = "containerInsights"
        value = "enabled"
      }
    }

    data "aws_iam_policy_document" "ecs_tasks_assume" {
      statement {
        actions = ["sts:AssumeRole"]
        principals {
          type        = "Service"
          identifiers = ["ecs-tasks.amazonaws.com"]
        }
      }
    }

    resource "aws_iam_role" "execution" {
      name               = "${var.name}-execution"
      assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
    }

    resource "aws_iam_role_policy_attachment" "execution" {
      role       = aws_iam_role.execution.name
      policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
    }

    resource "aws_security_group" "service" {
      name        = "${var.name}-service"
      description = "Ingress to the ${var.name} tasks"
      vpc_id      = var.vpc_id

      ingress {
        description = "HTTP from inside the VPC"
        from_port   = var.container_port
        to_port     = var.container_port
        protocol    = "tcp"
        cidr_blocks = ["10.0.0.0/8"]
      }

      egress {
        description = "Outbound HTTPS and database traffic"
        from_port   = 0
        to_port     = 0
        protocol    = "-1"
        cidr_blocks = ["0.0.0.0/0"]
      }
    }

    resource "aws_ecs_task_definition" "app" {
      family                   = var.name
      requires_compatibilities = ["FARGATE"]
      network_mode             = "awsvpc"
      cpu                      = 256
      memory                   = 512
      execution_role_arn       = aws_iam_role.execution.arn

      container_definitions = jsonencode([
        {
          name         = "api"
          image        = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
          essential    = true
          portMappings = [{ containerPort = var.container_port, protocol = "tcp" }]
          secrets      = local.secrets
          logConfiguration = {
            logDriver = "awslogs"
            options = {
              awslogs-group         = aws_cloudwatch_log_group.app.name
              awslogs-region        = var.region
              awslogs-stream-prefix = "api"
            }
          }
        }
      ])
    }

    resource "aws_ecs_service" "app" {
      name            = var.name
      cluster         = aws_ecs_cluster.this.id
      task_definition = aws_ecs_task_definition.app.arn
      desired_count   = var.desired_count
      launch_type     = "FARGATE"

      network_configuration {
        subnets          = var.subnet_ids
        security_groups  = [aws_security_group.service.id]
        assign_public_ip = false
      }

      deployment_circuit_breaker {
        enable   = true
        rollback = true
      }
    }
""")

TF_OUTPUTS = _src("""
    output "ecr_repository_url" {
      description = "Push the image here"
      value       = aws_ecr_repository.app.repository_url
    }

    output "ecs_service_name" {
      description = "Name of the ECS service"
      value       = aws_ecs_service.app.name
    }
""")


def render_devops(project: str, architecture: ArchitectureSpec) -> list[GeneratedFile]:
    port = architecture.deployment.container_port
    replicas = max(1, architecture.deployment.replicas)
    return [
        GeneratedFile(path="Dockerfile", content=render_dockerfile(port),
                      description="Multi-stage, non-root image with a health check"),
        GeneratedFile(path=".dockerignore", content=DOCKERIGNORE,
                      description="Keeps the build context small"),
        GeneratedFile(path=".github/workflows/ci.yml", content=render_ci(project),
                      description="Lint, test and image build on every push"),
        GeneratedFile(path="deploy/k8s/deployment.yaml",
                      content=render_k8s_deployment(project, port, replicas),
                      description="Deployment with probes, limits and a restricted security context"),
        GeneratedFile(path="deploy/k8s/service.yaml", content=render_k8s_service(project, port),
                      description="ClusterIP service"),
        GeneratedFile(path="deploy/k8s/kustomization.yaml", content=render_kustomization(project),
                      description="Kustomize entry point"),
        GeneratedFile(path="deploy/terraform/versions.tf", content=TF_VERSIONS,
                      description="Terraform and provider constraints"),
        GeneratedFile(path="deploy/terraform/variables.tf",
                      content=render_tf_variables(project, port, replicas),
                      description="Inputs: region, VPC, subnets, image tag"),
        GeneratedFile(path="deploy/terraform/main.tf", content=TF_MAIN,
                      description="ECR repository, ECS cluster, task definition and service"),
        GeneratedFile(path="deploy/terraform/outputs.tf", content=TF_OUTPUTS,
                      description="Repository URL and service name"),
    ]  # fmt: skip
