resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.name}/database-url"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql+asyncpg://%s:%s@%s/%s",
    aws_db_instance.this.username,
    random_password.db.result,
    aws_db_instance.this.endpoint,
    aws_db_instance.this.db_name,
  )
}

resource "aws_secretsmanager_secret" "api_keys" {
  name                    = "${var.name}/api-keys"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  secret_id     = aws_secretsmanager_secret.api_keys.id
  secret_string = var.api_keys
}

resource "aws_secretsmanager_secret" "anthropic" {
  count                   = var.anthropic_api_key == "" ? 0 : 1
  name                    = "${var.name}/anthropic-api-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "anthropic" {
  count         = var.anthropic_api_key == "" ? 0 : 1
  secret_id     = aws_secretsmanager_secret.anthropic[0].id
  secret_string = var.anthropic_api_key
}
