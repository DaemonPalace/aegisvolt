variable "env" { type = string }
variable "services" { type = list(string) }

resource "aws_cloudwatch_log_group" "svc" {
  for_each          = toset(var.services)
  name              = "/aegisvolt/${var.env}/${each.key}"
  retention_in_days = 7
}

# Canary gate reads this: any ERROR logged by a canary pod counts against promotion.
resource "aws_cloudwatch_log_metric_filter" "canary_errors" {
  for_each       = aws_cloudwatch_log_group.svc
  name           = "aegisvolt-${var.env}-${each.key}-canary-errors"
  log_group_name = each.value.name
  pattern        = "{ ($.level = \"ERROR\") && ($.version = \"canary\") }"
  metric_transformation {
    name          = "CanaryErrors"
    namespace     = "AegisVolt/${var.env}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "saga_compensations" {
  for_each       = aws_cloudwatch_log_group.svc
  name           = "aegisvolt-${var.env}-${each.key}-saga-compensations"
  log_group_name = each.value.name
  pattern        = "{ $.event = \"saga.compensated\" }"
  metric_transformation {
    name          = "SagaCompensations"
    namespace     = "AegisVolt/${var.env}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_query_definition" "by_correlation" {
  name            = "aegisvolt-${var.env}-by-correlation"
  log_group_names = [for g in aws_cloudwatch_log_group.svc : g.name]
  query_string    = <<-EOT
    fields @timestamp, service, level, event, msg, correlation_id
    | filter correlation_id = "REPLACE_WITH_SAGA_ID"
    | sort @timestamp asc
  EOT
}

# Step one of a trace: which sagas failed or compensated, with the correlation_id to paste into by-correlation.
resource "aws_cloudwatch_query_definition" "failed_sagas" {
  name            = "aegisvolt-${var.env}-failed-sagas"
  log_group_names = [for g in aws_cloudwatch_log_group.svc : g.name]
  query_string    = <<-EOT
    fields @timestamp, correlation_id, event, msg
    | filter event in ["saga.compensated", "saga.compensation_failed", "saga.step.failed"]
    | sort @timestamp desc
    | limit 20
  EOT
}

output "log_groups" { value = [for g in aws_cloudwatch_log_group.svc : g.name] }
