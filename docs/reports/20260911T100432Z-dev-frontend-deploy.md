# Frontend Deployment Report

| Field | Value |
|---|---|
| Timestamp (UTC) | 20260911T100432Z |
| Project | enterprise-knowledge-based-automation |
| Environment | dev |
| Git SHA | 055ba2b |
| AWS account | 890290782965 |
| AWS region | us-west-2 |

## Result

FAILED - Amplify build 2 did not succeed

| Item | Value |
|---|---|
| Amplify app | d39pgpamq0p0n8 |
| Branch / commit | aws-deployment @ 5b6ca17 |
| Amplify job | 2 |
| Site URL | https://aws-deployment.d39pgpamq0p0n8.amplifyapp.com |
| API URL (CloudFront) | https://d2jw2wchz5oekh.cloudfront.net |
| API origin (ALB) | ekba-dev-alb-374753528.us-west-2.elb.amazonaws.com |
| Terraform (envs/frontend) | Plan: 2 to import, 0 to add, 2 to change, 0 to destroy. |
| Backend wiring | deploy.sh ran (security group and/or CORS changed) |

## Cost

Amplify ~$0.04 per build; CloudFront ~$0 at demo traffic; ~$0/month idle.
The backend stack still burns ~$0.09/hour until ./scripts/destroy.sh.
