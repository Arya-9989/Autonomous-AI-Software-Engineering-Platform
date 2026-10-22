output "url" {
  description = "Public URL of the platform"
  value       = "${local.https ? "https" : "http"}://${aws_lb.this.dns_name}"
}

output "ecr_repository_url" {
  description = "Push images here, tagged with the value passed as image_tag"
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.this.name
}

output "log_group" {
  description = "CloudWatch log group with the JSON application logs"
  value       = aws_cloudwatch_log_group.app.name
}
