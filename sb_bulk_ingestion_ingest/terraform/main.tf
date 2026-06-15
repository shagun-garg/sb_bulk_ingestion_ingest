terraform {
  required_version = ">= 1.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ==================== DYNAMODB TABLES ====================

resource "aws_dynamodb_table" "bulk_jobs" {
  name         = "BulkJobs-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute_definition {
    name = "job_id"
    type = "S"
  }

  attribute_definition {
    name = "idempotency_key"
    type = "S"
  }

  attribute_definition {
    name = "environment"
    type = "S"
  }

  attribute_definition {
    name = "created_at"
    type = "S"
  }

  attribute_definition {
    name = "gsi_pk"
    type = "S"
  }

  global_secondary_index {
    name            = "IdempotencyKeyIndex"
    hash_key        = "idempotency_key"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "EnvCreatedAtIndex"
    hash_key        = "environment"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "AllCreatedAtIndex"
    hash_key        = "gsi_pk"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Environment = var.environment
    Project     = "SkyBridge-Bulk-Ingest"
  }
}

resource "aws_dynamodb_table" "audit_log" {
  name         = "AuditLog-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "target_id"
  range_key    = "timestamp_id"

  attribute_definition {
    name = "target_id"
    type = "S"
  }

  attribute_definition {
    name = "timestamp_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Environment = var.environment
    Project     = "SkyBridge-Bulk-Ingest"
  }
}

# ==================== S3 BUCKET ====================

resource "aws_s3_bucket" "digest_bucket" {
  bucket        = "${var.s3_bucket_name}-${var.environment}"
  force_destroy = true

  tags = {
    Environment = var.environment
    Project     = "SkyBridge-Bulk-Ingest"
  }
}

resource "aws_s3_bucket_public_access_block" "digest_bucket_access" {
  bucket = aws_s3_bucket.digest_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ==================== SYSTEMS MANAGER PARAMETERS ====================

resource "aws_ssm_parameter" "jwt_secret" {
  name        = "/skybridge/${var.environment}/jwt_secret"
  description = "HMAC secret key for JWT session validation"
  type        = "SecureString"
  value       = var.jwt_secret

  tags = {
    Environment = var.environment
  }
}

resource "aws_ssm_parameter" "api_keys" {
  name        = "/skybridge/${var.environment}/api_keys"
  description = "Comma-separated authorized API keys"
  type        = "SecureString"
  value       = var.api_keys

  tags = {
    Environment = var.environment
  }
}

# ==================== IAM ROLES & POLICIES ====================

resource "aws_iam_role" "lambda_exec" {
  name = "skybridge-ingest-lambda-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "lambda_least_privilege" {
  name        = "skybridge-ingest-lambda-least-privilege-${var.environment}"
  description = "Least privilege permissions for SkyBridge Bulk Ingestion Lambda"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # CloudWatch Logs
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_logs.arn}:*"
      },
      # S3 Access
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.digest_bucket.arn,
          "${aws_s3_bucket.digest_bucket.arn}/*"
        ]
      },
      # DynamoDB Access
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.bulk_jobs.arn,
          "${aws_dynamodb_table.bulk_jobs.arn}/index/*",
          aws_dynamodb_table.audit_log.arn,
          "${aws_dynamodb_table.audit_log.arn}/index/*"
        ]
      },
      # SSM Parameter Access
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = [
          aws_ssm_parameter.jwt_secret.arn,
          aws_ssm_parameter.api_keys.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_least_privilege.arn
}

# ==================== CLOUDWATCH LOG GROUP ====================

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/skybridge-bulk-ingestion-${var.environment}"
  retention_in_days = 30
}

# ==================== LAMBDA FUNCTION ====================

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/lambda_function_payload.zip"
}

resource "aws_lambda_function" "ingest_api" {
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  function_name    = "skybridge-bulk-ingestion-${var.environment}"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "main.handler"
  runtime          = "python3.11"
  architectures    = ["arm64"]
  timeout          = 900  # 15 minutes
  memory_size      = 1024

  environment {
    variables = {
      ENVIRONMENT         = var.environment
      DYNAMODB_TABLE_JOBS = aws_dynamodb_table.bulk_jobs.name
      DYNAMODB_TABLE_AUDIT = aws_dynamodb_table.audit_log.name
      S3_BUCKET_NAME      = aws_s3_bucket.digest_bucket.id
      SSM_JWT_PARAM       = aws_ssm_parameter.jwt_secret.name
      SSM_API_KEYS_PARAM  = aws_ssm_parameter.api_keys.name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda_logs
  ]
}

# ==================== API GATEWAY ====================

resource "aws_apigatewayv2_api" "api" {
  name          = "skybridge-bulk-ingestion-api-${var.environment}"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ingest_api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "any" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}
