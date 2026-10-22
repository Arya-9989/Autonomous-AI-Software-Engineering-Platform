# Copy to terraform.tfvars (git-ignored) and fill in. Never commit real keys.
region             = "us-east-1"
vpc_id             = "vpc-0123456789abcdef0"
public_subnet_ids  = ["subnet-aaaa1111", "subnet-bbbb2222"]
private_subnet_ids = ["subnet-cccc3333", "subnet-dddd4444"]
image_tag          = "0.1.0"
certificate_arn    = ""
api_keys           = "ci:replace-with-a-long-random-key"
