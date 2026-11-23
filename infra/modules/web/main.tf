variable "env" { type = string }
variable "account_id" { type = string }

# Frontend hosting (SPEC §12): an S3 static website per env. The Next.js static export and its
# config.json ({apiUrl, env}) are synced here by deploy.yml.
# ponytail: HTTP-only S3 website endpoint; put CloudFront in front if the UI needs HTTPS or a custom domain.
resource "aws_s3_bucket" "web" {
  bucket        = "aegisvolt-web-${var.env}-${var.account_id}"
  force_destroy = true # only build output lives here
}

resource "aws_s3_bucket_website_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  index_document { suffix = "index.html" }
  error_document { key = "404.html" }
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "web" {
  bucket     = aws_s3_bucket.web.id
  depends_on = [aws_s3_bucket_public_access_block.web]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadWebsite"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web.arn}/*"
    }]
  })
}

output "bucket" { value = aws_s3_bucket.web.bucket }
output "web_url" { value = "http://${aws_s3_bucket_website_configuration.web.website_endpoint}" }
