variable "env" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "instance_id" { type = string }

# Path: Client --HTTPS--> REST API --VPC Link (PrivateLink endpoint service on the NLB)--> NLB --> node:30080 --> gateway pod

resource "aws_lb" "nlb" {
  name               = "aegisvolt-nlb-${var.env}"
  internal           = true
  load_balancer_type = "network"
  subnets            = var.subnet_ids
}

resource "aws_lb_target_group" "gateway" {
  name               = "aegisvolt-tg-${var.env}"
  port               = 30080
  protocol           = "TCP"
  target_type        = "instance"
  vpc_id             = var.vpc_id
  preserve_client_ip = false # node always sees the NLB's private IP -> node SG can allow just the private subnets
  health_check {
    protocol = "TCP"
    port     = "30080"
  }
}

resource "aws_lb_target_group_attachment" "node" {
  target_group_arn = aws_lb_target_group.gateway.arn
  target_id        = var.instance_id
  port             = 30080
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.nlb.arn
  port              = 80
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }
}

# REST VPC Link (v1): AWS creates a VPC endpoint service on this NLB; API Gateway is its consumer.
# This PrivateLink connection is the diagram's "VPC Endpoint Privado".
resource "aws_api_gateway_vpc_link" "this" {
  name        = "aegisvolt-vpclink-${var.env}"
  target_arns = [aws_lb.nlb.arn]
}

resource "aws_api_gateway_rest_api" "this" {
  name = "aegisvolt-api-${var.env}"
  endpoint_configuration { types = ["REGIONAL"] }
}

resource "aws_api_gateway_resource" "graphql" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "graphql"
}

# ANY includes OPTIONS: CORS preflight is answered by the gateway pod (FastAPI CORSMiddleware).
resource "aws_api_gateway_method" "graphql" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  resource_id   = aws_api_gateway_resource.graphql.id
  http_method   = "ANY"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "graphql" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.graphql.id
  http_method             = aws_api_gateway_method.graphql.http_method
  type                    = "HTTP_PROXY"
  integration_http_method = "ANY"
  uri                     = "http://${aws_lb.nlb.dns_name}/graphql"
  connection_type         = "VPC_LINK"
  connection_id           = aws_api_gateway_vpc_link.this.id
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  triggers = {
    redeploy = sha1(jsonencode([aws_api_gateway_resource.graphql, aws_api_gateway_method.graphql, aws_api_gateway_integration.graphql]))
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_api_gateway_stage" "this" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = var.env
}

output "api_url" { value = "${aws_api_gateway_stage.this.invoke_url}/graphql" }
output "nlb_arn" { value = aws_lb.nlb.arn }
