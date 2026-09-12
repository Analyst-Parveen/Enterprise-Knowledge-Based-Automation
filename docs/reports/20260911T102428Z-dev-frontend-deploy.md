# Frontend Deployment Report

| Field | Value |
|---|---|
| Timestamp (UTC) | 20260911T102428Z |
| Project | enterprise-knowledge-based-automation |
| Environment | dev |
| Git SHA | 6a3e2ef |
| AWS account | 890290782965 |
| AWS region | us-west-2 |

## Result

FAILED VERIFICATION - 2 of 12 checks failed

| Item | Value |
|---|---|
| Amplify app | d39pgpamq0p0n8 |
| Branch / commit | aws-deployment @ 5b6ca17 |
| Amplify job | 3 |
| Site URL | https://aws-deployment.d39pgpamq0p0n8.amplifyapp.com |
| API URL (CloudFront) | https://d2jw2wchz5oekh.cloudfront.net |
| API origin (ALB) | ekba-dev-alb-374753528.us-west-2.elb.amazonaws.com |
| Terraform (envs/frontend) | Plan: 0 to add, 1 to change, 0 to destroy. |
| Backend wiring | none needed |

## Verification

| Result | Check |
|---|---|
| FAIL | Amplify: latest build SUCCEED - got: SUCCEED	HEAD
None |
| PASS | HTTPS site https://aws-deployment.d39pgpamq0p0n8.amplifyapp.com/ -> 200 |
| PASS | page route /chat -> 200 |
| FAIL | unknown path -> 404 - got 301 |
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
