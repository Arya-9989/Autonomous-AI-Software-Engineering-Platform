variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "autoswe"
}

variable "vpc_id" {
  description = "Existing VPC to deploy into"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets (at least two AZs) for the load balancer"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnets (at least two AZs, with NAT egress) for ECS tasks and RDS"
  type        = list(string)
}

variable "image_tag" {
  description = "Immutable image tag in the ECR repository (e.g. the git SHA)"
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS. Leave empty to serve plain HTTP (not for production)."
  type        = string
  default     = ""
}

variable "allowed_cidrs" {
  description = "CIDR ranges allowed to reach the load balancer"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "desired_count" {
  description = "Number of Fargate tasks"
  type        = number
  default     = 2
}

variable "task_cpu" {
  description = "Fargate task CPU units"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 1024
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "api_keys" {
  description = "Comma-separated principal:key pairs for the platform API"
  type        = string
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key. Empty = only the offline provider is enabled."
  type        = string
  default     = ""
  sensitive   = true
}
