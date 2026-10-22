locals {
  secret_arns = concat(
    [aws_secretsmanager_secret.database_url.arn, aws_secretsmanager_secret.api_keys.arn],
    aws_secretsmanager_secret.anthropic[*].arn,
  )

  container_secrets = concat(
    [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "API_KEYS", valueFrom = aws_secretsmanager_secret.api_keys.arn },
    ],
    [for arn in aws_secretsmanager_secret.anthropic[*].arn : { name = "ANTHROPIC_API_KEY", valueFrom = arn }],
  )

  healthcheck = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
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

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.secret_arns
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

# The application itself needs no AWS permissions.
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name                   = "api"
    image                  = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential              = true
    readonlyRootFilesystem = true
    user                   = "10001"
    stopTimeout            = 45
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "LOG_FORMAT", value = "json" },
      # Concurrent task start-ups are serialised by a PostgreSQL advisory lock.
      { name = "DB_AUTO_MIGRATE", value = "true" },
      # Only the ALB can reach the tasks (see network.tf), so its headers are trusted.
      { name = "FORWARDED_ALLOW_IPS", value = "*" },
    ]
    secrets     = local.container_secrets
    mountPoints = [{ sourceVolume = "tmp", containerPath = "/tmp" }]
    healthCheck = {
      command     = ["CMD", "python", "-c", local.healthcheck]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
  }])

  volume {
    name = "tmp"
  }
}

resource "aws_ecs_service" "app" {
  name                              = var.name
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.app.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}
