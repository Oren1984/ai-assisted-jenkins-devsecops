# SCENARIO FIXTURE (terraform-failure): identical to terraform/main.tf except for one
# deliberate error: aws_vpc.example uses the misspelled argument "cidr_blok".

provider "aws" {
  region = var.aws_region
}

resource "aws_vpc" "example" {
  cidr_blok            = var.vpc_cidr_block
  enable_dns_hostnames = true

  tags = {
    Name    = "ai-jenkins-devsecops-example"
    Purpose = "validation-only"
  }
}

resource "aws_subnet" "app" {
  vpc_id                  = aws_vpc.example.id
  cidr_block              = var.app_subnet_cidr_block
  map_public_ip_on_launch = false

  tags = {
    Name = "ai-jenkins-devsecops-app"
  }
}

resource "aws_security_group" "app" {
  name        = "ai-jenkins-devsecops-app"
  description = "Allow the sample application port inside the VPC only"
  vpc_id      = aws_vpc.example.id

  ingress {
    description = "Sample application"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr_block]
  }
}
