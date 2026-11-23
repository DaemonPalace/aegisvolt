terraform {
  required_version = ">= 1.10"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # State per workspace lands at env:/<workspace>/aegisvolt/terraform.tfstate (versioned bucket).
  backend "s3" {
    bucket       = "aegisvolt-tfstate-272973566676"
    key          = "aegisvolt/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

locals {
  env = terraform.workspace

  # One entry per static environment. Instance type must be Free-plan eligible (account restriction): m7i-flex.large = 2 vCPU / 8 GiB. Any other workspace (incl. "default") fails the guard below.
  envs = {
    dev  = { cidr = "10.10.0.0/16", instance_type = "m7i-flex.large" }
    test = { cidr = "10.20.0.0/16", instance_type = "m7i-flex.large" }
    prod = { cidr = "10.30.0.0/16", instance_type = "m7i-flex.large" }
  }
  cfg  = lookup(local.envs, local.env, local.envs.dev)
  seed = jsondecode(file("${path.module}/../seed/seed.json"))
}

resource "terraform_data" "workspace_guard" {
  lifecycle {
    precondition {
      condition     = contains(keys(local.envs), local.env)
      error_message = "Workspace '${local.env}' is not an AegisVolt environment. Run: terraform workspace select dev|test|prod"
    }
  }
}

provider "aws" {
  region = "us-east-1"
  default_tags { tags = { Project = "aegisvolt", Env = local.env } }
}

data "aws_caller_identity" "me" {}

module "network" {
  source = "./modules/network"
  env    = local.env
  cidr   = local.cfg.cidr
}

module "data" {
  source = "./modules/data"
  env    = local.env
  seed   = local.seed
}

module "observability" {
  source   = "./modules/observability"
  env      = local.env
  services = ["station-reservation-service", "billing-wallet-service", "energy-dispatch-service", "graphql-gateway"]
}

module "k3s" {
  source        = "./modules/k3s"
  env           = local.env
  instance_type = local.cfg.instance_type
  subnet_id     = module.network.private_subnet_ids[0]
  sg_id         = module.network.node_sg_id
  account_id    = data.aws_caller_identity.me.account_id
  boundary_arn  = "arn:aws:iam::${data.aws_caller_identity.me.account_id}:policy/exam-aegisvolt-boundary"
}

module "apigw" {
  source      = "./modules/apigw"
  env         = local.env
  vpc_id      = module.network.vpc_id
  subnet_ids  = module.network.private_subnet_ids
  instance_id = module.k3s.instance_id
}

module "web" {
  source     = "./modules/web"
  env        = local.env
  account_id = data.aws_caller_identity.me.account_id
}

output "api_url" { value = module.apigw.api_url }
output "web_url" { value = module.web.web_url }
output "web_bucket" { value = module.web.bucket }
output "nlb_arn" { value = module.apigw.nlb_arn }
output "instance_id" { value = module.k3s.instance_id }
output "tables" { value = module.data.tables }
output "log_groups" { value = module.observability.log_groups }
output "admin_token" {
  value     = module.k3s.admin_token
  sensitive = true
}
