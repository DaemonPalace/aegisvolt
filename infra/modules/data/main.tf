variable "env" { type = string }
variable "seed" { type = any }

# Database-per-service: each microservice owns its table; flags + sagas belong to the gateway.
locals {
  tables = {
    reservations = "pk_sk"
    billing      = "pk_sk"
    dispatch     = "pk_sk"
    sagas        = "pk_sk"
    flags        = "name"
  }

  connectors = merge([
    for s in var.seed.stations : {
      for c in var.seed.connectors : "${s.id}-${c.suffix}" => {
        station_id = s.id
        type       = c.type
        power_kw   = c.power_kw
      }
    }
  ]...)
}

resource "aws_dynamodb_table" "this" {
  for_each     = local.tables
  name         = "aegisvolt-${each.key}-${var.env}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value == "name" ? "name" : "pk"
  range_key    = each.value == "name" ? null : "sk"

  attribute {
    name = each.value == "name" ? "name" : "pk"
    type = "S"
  }
  dynamic "attribute" {
    for_each = each.value == "name" ? [] : ["sk"]
    content {
      name = "sk"
      type = "S"
    }
  }
}

# ---------- seed data: created once, then owned by the app (ignore_changes) ----------
resource "aws_dynamodb_table_item" "station" {
  for_each   = { for s in var.seed.stations : s.id => s }
  table_name = aws_dynamodb_table.this["reservations"].name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk   = { S = "STATION#${each.key}" }
    sk   = { S = "META" }
    name = { S = each.value.name }
    city = { S = each.value.city }
  })
  lifecycle { ignore_changes = [item] }
}

resource "aws_dynamodb_table_item" "connector" {
  for_each   = local.connectors
  table_name = aws_dynamodb_table.this["reservations"].name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk       = { S = "STATION#${each.value.station_id}" }
    sk       = { S = "CONNECTOR#${each.key}" }
    type     = { S = each.value.type }
    power_kw = { N = tostring(each.value.power_kw) }
    status   = { S = "AVAILABLE" }
  })
  lifecycle { ignore_changes = [item] }
}

resource "aws_dynamodb_table_item" "wallet" {
  for_each   = { for f in var.seed.fleets : f.id => f }
  table_name = aws_dynamodb_table.this["billing"].name
  hash_key   = "pk"
  range_key  = "sk"
  item = jsonencode({
    pk            = { S = "FLEET#${each.key}" }
    sk            = { S = "WALLET" }
    balance_cents = { N = tostring(each.value.balance_cents) }
  })
  lifecycle { ignore_changes = [item] }
}

# Per-environment flag defaults. Runtime toggles survive later applies (ignore_changes).
resource "aws_dynamodb_table_item" "flag" {
  for_each   = var.seed.flags
  table_name = aws_dynamodb_table.this["flags"].name
  hash_key   = "name"
  item = jsonencode({
    name        = { S = each.key }
    enabled     = { BOOL = each.value[var.env] }
    description = { S = each.value.description }
  })
  lifecycle { ignore_changes = [item] }
}

output "tables" { value = { for k, t in aws_dynamodb_table.this : k => t.name } }
