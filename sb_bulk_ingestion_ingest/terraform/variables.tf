variable "aws_region" {
  type        = string
  description = "AWS region to deploy resources"
  default     = "us-east-1"
}

variable "environment" {
  type        = string
  description = "Target environment: uat, production, sandbox, prod"
  default     = "uat"
}

variable "jwt_secret" {
  type        = string
  description = "HMAC secret key for JWT session verification"
  default     = "super-secret-skybridge-session-key-2026"
  sensitive   = true
}

variable "api_keys" {
  type        = string
  description = "Comma-separated list of live API keys for API Key auth"
  default     = "skybridge-key-operator-1234,skybridge-key-viewer-5678"
  sensitive   = true
}

variable "s3_bucket_name" {
  type        = string
  description = "Name of the S3 bucket to create for settlement ingestion CSV digests"
  default     = "skybridge-settlement-ingestion-digest-2026"
}
