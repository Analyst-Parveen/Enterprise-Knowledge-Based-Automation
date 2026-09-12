##############################################################################
# Network - public subnets for the ALB and tasks, private subnets for RDS.
# NO NAT GATEWAY.
#
# A NAT Gateway costs ~$32/month plus data, which alone would consume more than
# the entire $20 budget. Fargate tasks run in public subnets with a public IP so
# they can reach ECR, Bedrock and Transcribe directly. The security group - not
# the subnet - is what keeps them private: nothing but the ALB can reach the
# tasks, and the ALB only accepts traffic from the operator's own IP.
#
# The private subnets hold only the RDS database. Their route table has no
# internet route at all (the database needs none), so they cost nothing and
# need no NAT.
#
# See .claude/rules/aws-infrastructure.md section 5.
##############################################################################

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  # Two AZs: the minimum an ALB requires.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name}-igw" }
}

resource "aws_subnet" "public" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.name}-public-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.name}-public" }
}

resource "aws_route_table_association" "public" {
  count = length(aws_subnet.public)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private subnets - RDS only. A DB subnet group needs two AZs even for a
# single-AZ instance. Offset 10 keeps them clear of the public /24s.
resource "aws_subnet" "private" {
  count = length(local.azs)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "${var.name}-private-${local.azs[count.index]}" }
}

# No routes beyond the implicit VPC-local one: nothing in these subnets can
# reach, or be reached from, the internet.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.name}-private" }
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

##############################################################################
# Security groups
##############################################################################

# AWS-managed list of the addresses CloudFront uses to reach origins.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  count = var.cloudfront_origin_ingress ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

# ALB: reachable only from the operator's own IP, never 0.0.0.0/0 - and, when
# cloudfront_origin_ingress is on, from CloudFront (the HTTPS API front door
# for the Amplify frontend).
resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "ALB ingress, restricted to the operator CIDR"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from the allowed CIDR only"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  dynamic "ingress" {
    for_each = var.cloudfront_origin_ingress ? [1] : []
    content {
      description     = "HTTP from CloudFront origin-facing ranges (HTTPS API front door)"
      from_port       = 80
      to_port         = 80
      protocol        = "tcp"
      prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront[0].id]
    }
  }

  ingress {
    description = "HTTPS from the allowed CIDR only"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-alb" }
}

# Tasks: reachable ONLY from the ALB security group. This references the SG
# rather than a CIDR, so nothing else in the VPC can reach the containers.
resource "aws_security_group" "tasks" {
  name        = "${var.name}-tasks"
  description = "Fargate tasks - ingress from the ALB security group only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Backend API from the ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Frontend from the ALB"
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Outbound to ECR, Bedrock, Transcribe, S3"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-tasks" }
}

# Database: PostgreSQL from the task security group ONLY. No CIDR rule, no
# egress rule (Terraform removes the AWS default allow-all egress) - the
# database never initiates connections.
resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "RDS PostgreSQL - ingress from the Fargate task security group only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from the backend tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }

  tags = { Name = "${var.name}-db" }
}
