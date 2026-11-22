# One-time bootstrap: remote-state bucket + GitHub Actions roles.
# Run with: AWS_PROFILE=aegisvolt terraform -chdir=bootstrap apply
# Its own state lives in the bucket it creates (key bootstrap/terraform.tfstate).

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket       = "aegisvolt-tfstate-272973566676"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags { tags = { Project = "aegisvolt", Env = "shared" } }
}

data "aws_caller_identity" "me" {}

locals {
  # The repo uses GitHub's immutable OIDC subject: owner and repo carry their numeric IDs
  # (gh api repos/DaemonPalace/aegisvolt/actions/oidc/customization/sub).
  sub_prefix   = "repo:DaemonPalace@176678192/aegisvolt@1467661995"
  state_bucket = "aegisvolt-tfstate-${data.aws_caller_identity.me.account_id}"
  boundary_arn = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:policy/exam-aegisvolt-boundary"
}

# ---------- state bucket (versioned = history of every tfstate) ----------
resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

# ---------- GitHub OIDC: provider already exists (shared with SpottyDaemon) — read only ----------
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trust" {
  for_each = {
    plan   = ["${local.sub_prefix}:pull_request"]
    deploy = [for b in ["dev", "test", "prod"] : "${local.sub_prefix}:ref:refs/heads/${b}"]
  }
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value
    }
  }
}

# PR role: read-only + state bucket read/write (plan writes the lockfile).
resource "aws_iam_role" "plan" {
  name                 = "aegisvolt-gha-plan"
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  permissions_boundary = local.boundary_arn
}

resource "aws_iam_role_policy_attachment" "plan_readonly" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "plan_state" {
  name = "state-bucket"
  role = aws_iam_role.plan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
      Resource = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    }]
  })
}

# Deploy role: same scoped permissions as the human operator (single source of truth).
resource "aws_iam_role" "deploy" {
  name                 = "aegisvolt-gha-deploy"
  assume_role_policy   = data.aws_iam_policy_document.trust["deploy"].json
  permissions_boundary = local.boundary_arn
}

resource "aws_iam_role_policy" "deploy" {
  name   = "operator-scope"
  role   = aws_iam_role.deploy.id
  policy = file("${path.module}/operator-policy.json")
}

output "state_bucket" { value = aws_s3_bucket.state.bucket }
output "plan_role_arn" { value = aws_iam_role.plan.arn }
output "deploy_role_arn" { value = aws_iam_role.deploy.arn }
