variable "aws_region" {
  description = "AWS region the example network would live in."
  type        = string
  default     = "eu-west-1"
}

variable "vpc_cidr_block" {
  description = "CIDR block of the example VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "app_subnet_cidr_block" {
  description = "CIDR block of the private application subnet."
  type        = string
  default     = "10.20.1.0/24"
}
