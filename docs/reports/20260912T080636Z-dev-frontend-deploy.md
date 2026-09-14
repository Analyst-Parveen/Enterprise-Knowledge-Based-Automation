# Frontend Deployment Report

| Field | Value |
|---|---|
| Timestamp (UTC) | 20260912T080636Z |
| Project | enterprise-knowledge-based-automation |
| Environment | dev |
| Git SHA | 5fbdac2 |
| AWS account | 890290782965 |
| AWS region | us-west-2 |

## Result

FAILED VERIFICATION - 1 of 12 checks failed

| Item | Value |
|---|---|
| Amplify app | d39pgpamq0p0n8 |
| Branch / commit | aws-deployment @ 0670632 |
| Amplify job | 9 |
| Site URL | https://aws-deployment.d39pgpamq0p0n8.amplifyapp.com |
| API URL (CloudFront) | https://d2jw2wchz5oekh.cloudfront.net |
| API origin (ALB) | ekba-dev-alb-374753528.us-west-2.elb.amazonaws.com |
| Terraform (envs/frontend) | No changes. Your infrastructure matches the configuration. |
| Backend wiring | none needed |

## Verification

| Result | Check |
|---|---|
| FAIL | Amplify: latest build SUCCEED - got: RUNNING	5fbdac2a07e078d1cec4d119b96ed5ab5c62ed3d |
| PASS | HTTPS site https://aws-deployment.d39pgpamq0p0n8.amplifyapp.com/ -> 200 |
| PASS | page route /chat -> 200 |
| PASS | unknown path -> the 404 page |
| PASS | HSTS header present |
| PASS | CSP header present |
| PASS | HTTP redirects to HTTPS |
| PASS | deployed bundle calls https://d2jw2wchz5oekh.cloudfront.net |
| PASS | API health through CloudFront -> 200 |
| PASS | CORS preflight from https://aws-deployment.d39pgpamq0p0n8.amplifyapp.com allowed |
| PASS | API enforces auth (no token -> 401) |
| PASS | Authorization header reaches the API through CloudFront |

## Cost

Amplify ~$0.04 per build; CloudFront ~$0 at demo traffic; ~$0/month idle.
The backend stack still burns ~$0.09/hour until ./scripts/destroy.sh.
