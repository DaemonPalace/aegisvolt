variable "env" { type = string }
variable "cidr" { type = string }

locals {
  azs  = ["us-east-1a", "us-east-1b"] # 2 AZs: the NLB requires subnets in >=2; compute lives in AZ-a only
  name = "aegisvolt-${var.env}"
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true # required for interface-endpoint private DNS
  tags                 = { Name = local.name }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr, 8, count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

# ponytail: single NAT in AZ-a (cost); one NAT per AZ if compute ever spreads across AZs.
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat" }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = local.name }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${local.name}-public" }
}

# Private route table: local VPC route (implicit) + default via NAT (only for pulling images / k3s install)
# + S3/DynamoDB prefix-list routes added by the gateway endpoints below.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ---------- VPC endpoints ----------
resource "aws_vpc_endpoint" "gateway" {
  for_each          = toset(["s3", "dynamodb"])
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.us-east-1.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${local.name}-${each.key}" }
}

resource "aws_security_group" "vpce" {
  name        = "${local.name}-vpce"
  description = "Interface endpoints: HTTPS from inside the VPC"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.cidr]
  }
  tags = { Name = "${local.name}-vpce" }
}

# ponytail: CloudWatch Logs endpoint in AZ-a only (where the node runs); add AZ-b for HA.
resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.us-east-1.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.private[0].id]
  security_group_ids  = [aws_security_group.vpce.id]
  private_dns_enabled = true
  tags                = { Name = "${local.name}-logs" }
}

# Node: NodePort 30080 reachable only from the private subnets (where the NLB lives). No SSH; access is SSM.
resource "aws_security_group" "node" {
  name        = "${local.name}-node"
  description = "k3s node: gateway NodePort from NLB only"
  vpc_id      = aws_vpc.this.id
  ingress {
    from_port   = 30080
    to_port     = 30080
    protocol    = "tcp"
    cidr_blocks = aws_subnet.private[*].cidr_block
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${local.name}-node" }
}

output "vpc_id" { value = aws_vpc.this.id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "node_sg_id" { value = aws_security_group.node.id }
