output "api_gateway_url" {
  description = "URL of the API Gateway HTTP API"
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "lambda_arn" {
  description = "ARN of the Bulk Ingestion Lambda function"
  value       = aws_lambda_function.ingest_api.arn
}

output "dynamodb_table_jobs_name" {
  description = "Name of the DynamoDB Jobs table"
  value       = aws_dynamodb_table.bulk_jobs.name
}

output "dynamodb_table_audit_name" {
  description = "Name of the DynamoDB Audit Log table"
  value       = aws_dynamodb_table.audit_log.name
}

output "s3_bucket_name" {
  description = "Name of the S3 bucket created"
  value       = aws_s3_bucket.digest_bucket.id
}
