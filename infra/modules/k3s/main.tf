variable "env" { type = string }
variable "instance_type" { type = string }
variable "subnet_id" { type = string }
variable "sg_id" { type = string }
variable "account_id" { type = string }
variable "boundary_arn" { type = string }

# ponytail: single-node k3s instead of EKS (EKS control plane ~$73/mo per env is outside free tier/credits budget).
# Same Kubernetes API; swap this module for terraform-aws-modules/eks to get EKS.

locals { name = "aegisvolt-node-${var.env}" }

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "random_password" "admin_token" {
  length  = 32
  special = false
}

# ---------- instance role (pods use it through IMDSv2, hop limit 2) ----------
resource "aws_iam_role" "node" {
  name                 = local.name
  permissions_boundary = var.boundary_arn
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "app" {
  name = "app"
  role = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "OwnEnvTablesOnly"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:ConditionCheckItem", "dynamodb:DescribeTable"]
        Resource = "arn:aws:dynamodb:us-east-1:${var.account_id}:table/aegisvolt-*-${var.env}"
      },
      {
        Sid      = "OwnEnvLogsOnly"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "arn:aws:logs:us-east-1:${var.account_id}:log-group:/aegisvolt/${var.env}/*"
      },
      {
        Sid      = "CanaryGateReadsMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricStatistics", "cloudwatch:GetMetricData"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "node" {
  name = local.name
  role = aws_iam_role.node.name
}

# ---------- the node ----------
resource "aws_instance" "node" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.sg_id]
  iam_instance_profile   = aws_iam_instance_profile.node.name

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # pods are one extra hop away from IMDS
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  # ponytail: ADMIN_TOKEN travels in user_data (readable via ec2:DescribeInstanceAttribute); fine for a demo flag-toggle token.
  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    curl -sfL https://get.k3s.io | INSTALL_K3S_SKIP_SELINUX_RPM=true \
      INSTALL_K3S_EXEC="--write-kubeconfig-mode 644 --disable traefik --disable servicelb" sh -
    until /usr/local/bin/kubectl get nodes | grep -q ' Ready'; do sleep 5; done
    /usr/local/bin/kubectl create namespace aegisvolt
    /usr/local/bin/kubectl -n aegisvolt create secret generic aegisvolt-secrets \
      --from-literal=ADMIN_TOKEN='${random_password.admin_token.result}'
  EOT

  # AMI parameter moves weekly; don't replace the node (and the cluster) because of it.
  lifecycle { ignore_changes = [ami] }

  tags = { Name = local.name }
}

output "instance_id" { value = aws_instance.node.id }
output "admin_token" {
  value     = random_password.admin_token.result
  sensitive = true
}
