# Frontend Deployment Report

| Field | Value |
|---|---|
| Timestamp (UTC) | 20260911T091627Z |
| Project | enterprise-knowledge-based-automation |
| Environment | dev |
| Git SHA | 89f623b |
| AWS account | 890290782965 |
| AWS region | us-west-2 |

## Result

PENDING - CloudFront API front door applied; Amplify app not connected yet

| Item | Value |
|---|---|
| Amplify app | not connected yet |
| Branch / commit | aws-deployment @ 89f623b |
| Amplify job | none started |
| Site URL | n/a |
| API URL (CloudFront) | https://d2jw2wchz5oekh.cloudfront.net |
| API origin (ALB) | ekba-dev-alb-374753528.us-west-2.elb.amazonaws.com |
| Terraform (envs/frontend) | Plan: 2 to add, 0 to change, 0 to destroy. |
| Backend wiring | none needed |

## Cost

Amplify ~$0.04 per build; CloudFront ~$0 at demo traffic; ~$0/month idle.
The backend stack still burns ~$0.09/hour until ./scripts/destroy.sh.
