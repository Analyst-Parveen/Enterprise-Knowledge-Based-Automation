"""Synthetic document bodies for the demo knowledge base.

These are indexed through the REAL chunk -> embed -> Qdrant path by seeds.seed,
so retrieval, citations and confidence in the demo are genuine rather than
fabricated rows.

All content is invented. No real company policies and no personal data.

The facts here are the ground truth for evaluation/datasets/golden_set.json -
changing a figure below means updating the golden set too.
"""

from __future__ import annotations

# document_id -> list of (page_number, section, body)
SEED_DOCUMENT_BODIES: dict[str, list[tuple[int | None, str | None, str]]] = {
    # -----------------------------------------------------------------------
    "seed-doc-travel-2026": [
        (
            1,
            "Scope",
            "This Travel and Expense Policy takes effect on 1 January 2026 and "
            "supersedes the 2024 policy. It applies to all employees and "
            "contractors travelling on company business.",
        ),
        (
            4,
            "Accommodation",
            "Employees may claim up to 150 USD per night for domestic hotel "
            "accommodation. International accommodation is capped at 220 USD per "
            "night. Receipts are required for all claims above 25 USD. Bookings "
            "must be made through the approved travel portal wherever possible.",
        ),
        (
            5,
            "Meals",
            "The daily meal allowance for domestic travel is 60 USD per day. For "
            "international travel the allowance is 85 USD per day. Alcohol is not "
            "reimbursable under any circumstances.",
        ),
        (
            6,
            "Ground transport",
            "Standard-class rail and economy air fares are reimbursed in full. "
            "Ride-hailing and taxi journeys are reimbursed up to 75 USD per day. "
            "Personal vehicle mileage is reimbursed at 0.58 USD per mile.",
        ),
        (
            9,
            "Approval and submission",
            "Any single expense above 500 USD requires written approval from a "
            "department head before the expense is incurred. Expense claims must "
            "be submitted within 30 days of the trip ending. Claims submitted "
            "after 60 days will not be reimbursed.",
        ),
    ],
    # -----------------------------------------------------------------------
    # The superseded version. Its figures differ deliberately so the
    # policy-comparison workflow has something real to compare.
    "seed-doc-travel-2024": [
        (
            1,
            "Scope",
            "This Travel and Expense Policy took effect on 1 January 2024. It has "
            "been superseded by the 2026 policy and is retained for reference.",
        ),
        (
            3,
            "Accommodation",
            "Employees may claim up to 120 USD per night for domestic hotel "
            "accommodation. International accommodation is capped at 180 USD per "
            "night. Receipts are required for all claims above 20 USD.",
        ),
        (
            4,
            "Meals",
            "The daily meal allowance for domestic travel is 50 USD per day. For "
            "international travel the allowance is 70 USD per day.",
        ),
        (
            5,
            "Ground transport",
            "Standard-class rail and economy air fares are reimbursed in full. "
            "Taxi journeys are reimbursed up to 60 USD per day. Personal vehicle "
            "mileage is reimbursed at 0.52 USD per mile.",
        ),
        (
            8,
            "Approval and submission",
            "Any single expense above 750 USD requires written approval from a "
            "department head. Expense claims must be submitted within 45 days of "
            "the trip ending.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-onboarding": [
        (
            1,
            "Purpose",
            "This standard operating procedure covers onboarding a new employee "
            "or contractor from offer acceptance through the end of week one.",
        ),
        (
            2,
            "Day one equipment",
            "Every new starter is issued a laptop, a monitor, a headset and a "
            "physical access badge on their first day. IT prepares the laptop at "
            "least two working days before the start date. Contractors receive "
            "the same equipment but on a returnable asset agreement.",
        ),
        (
            3,
            "Accounts and access",
            "The hiring manager raises an access request at least five working "
            "days before the start date. Accounts are provisioned with "
            "least-privilege defaults; elevated access requires a separate "
            "approval from the system owner.",
        ),
        (
            5,
            "Contractor onboarding",
            "Contractors additionally require a signed non-disclosure agreement "
            "before any system access is granted, and their accounts are created "
            "with a fixed expiry date matching the contract end date.",
        ),
        (
            7,
            "First week checklist",
            "Manager introduction, security awareness training, payroll setup and "
            "a 30-60-90 day plan must all be completed within the first week.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-leave": [
        (
            1,
            "Annual leave entitlement",
            "Full-time employees accrue 25 days of annual leave per calendar "
            "year, in addition to public holidays. Part-time entitlement is "
            "pro-rated against contracted hours.",
        ),
        (
            2,
            "Carry-over",
            "A maximum of 5 days of unused annual leave may be carried over into "
            "the following calendar year. Carried-over days must be used before "
            "31 March or they are forfeited.",
        ),
        (
            3,
            "Requesting leave",
            "Leave requests are submitted through the HR portal and require "
            "manager approval. Requests of five consecutive days or more should "
            "be submitted at least three weeks in advance.",
        ),
        (
            4,
            "Sick leave",
            "Employees are entitled to 10 days of paid sick leave per year. "
            "Absences of more than three consecutive days require a medical "
            "certificate.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-nda": [
        (
            1,
            "Definition of confidential information",
            "Confidential information includes technical data, customer lists, "
            "pricing, source code, and any material marked confidential or which "
            "a reasonable person would understand to be confidential.",
        ),
        (
            2,
            "Term",
            "This agreement remains in force for a period of three years from the "
            "date of signature. Obligations relating to trade secrets continue "
            "for as long as the information remains a trade secret.",
        ),
        (
            3,
            "Permitted disclosure",
            "The receiving party may disclose confidential information where "
            "required by law, provided it gives the disclosing party prompt "
            "written notice and reasonable opportunity to object.",
        ),
        (
            4,
            "Return of materials",
            "On termination, the receiving party must return or destroy all "
            "confidential material within 30 days and certify destruction in "
            "writing on request.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-q3-spend": [
        (
            None,
            "Q3 departmental spend",
            "department: Finance | budget_usd: 240000 | actual_usd: 231400 | variance_pct: -3.6\n"
            "department: HR | budget_usd: 180000 | actual_usd: 176900 | variance_pct: -1.7\n"
            "department: Legal | budget_usd: 150000 | actual_usd: 148200 | variance_pct: -1.2\n"
            "department: Sales | budget_usd: 420000 | actual_usd: 468300 | variance_pct: 11.5\n"
            "department: Marketing | budget_usd: 310000 | actual_usd: 352100 | "
            "variance_pct: 13.6\n"
            "department: Operations | budget_usd: 275000 | actual_usd: 268400 | "
            "variance_pct: -2.4\n"
            "department: Technical | budget_usd: 510000 | actual_usd: 497800 | variance_pct: -2.4",
        ),
        (
            None,
            "Commentary",
            "Marketing and Sales both exceeded their Q3 budgets, driven by "
            "additional spend on the Q4 product launch campaign. All other "
            "departments came in under budget.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-arch": [
        (
            None,
            "Platform architecture",
            "Architecture diagram description: the Next.js frontend calls the "
            "FastAPI backend over HTTPS through an application load balancer. "
            "The backend reads and writes PostgreSQL for relational data, Qdrant "
            "for vector search, and Redis for caching and rate limiting. "
            "Documents are stored in Amazon S3. Model inference and embeddings "
            "are served by Amazon Bedrock, and speech-to-text by Amazon "
            "Transcribe. Every request carries a correlation ID from the browser "
            "through to the AI call.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-security": [
        (
            1,
            "Severity definitions",
            "A severity one incident is a confirmed breach, data loss, or a full "
            "outage of a customer-facing service. Severity two is a partial "
            "outage or degraded service with no data loss.",
        ),
        (
            2,
            "Escalation path",
            "For a severity one incident the on-call engineer is paged first and "
            "must acknowledge within 15 minutes. The on-call engineer then "
            "escalates to the security lead and the engineering manager. If the "
            "on-call engineer does not acknowledge within 15 minutes, the page "
            "automatically escalates to the secondary on-call rota.",
        ),
        (
            3,
            "Containment",
            "Containment takes priority over root-cause analysis. Isolate the "
            "affected system, revoke potentially compromised credentials, and "
            "preserve logs before making any remediation change.",
        ),
        (
            4,
            "Communication",
            "The incident commander posts an update every 30 minutes for the "
            "duration of a severity one incident. Customer communication is "
            "issued only by the communications lead.",
        ),
        (
            5,
            "Post-incident review",
            "A blameless post-incident review is held within five working days. "
            "The review output is a written timeline and a list of owned, dated "
            "follow-up actions.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-pitch": [
        (
            2,
            "Positioning",
            "The platform is positioned as a private, tenant-isolated knowledge "
            "assistant for regulated industries where sending documents to a "
            "public assistant is not acceptable.",
        ),
        (
            5,
            "Discovery questions",
            "Ask where institutional knowledge currently lives, how long a new "
            "starter takes to become productive, and what happens today when a "
            "policy changes.",
        ),
        (
            11,
            "Objection handling",
            "On accuracy: every answer carries citations to the source document "
            "and page, and the assistant declines to answer when the knowledge "
            "base does not contain the answer. On data residency: documents stay "
            "within the customer's own cloud account.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-brand": [
        (
            1,
            "Voice principles",
            "Write plainly and specifically. Prefer concrete nouns and active "
            "verbs. Never claim a capability the product does not have.",
        ),
        (
            3,
            "Terminology",
            "Use 'knowledge base', not 'corpus'. Use 'assistant', not 'bot'. "
            "Capitalise product names exactly as registered.",
        ),
        (
            5,
            "Claims and compliance",
            "Any performance or accuracy claim in external material must be "
            "supported by a documented measurement, and must be reviewed by "
            "Legal before publication.",
        ),
    ],
    # -----------------------------------------------------------------------
    "seed-doc-warehouse": [
        (
            1,
            "Personal protective equipment",
            "Steel-toed safety boots and a high-visibility vest are mandatory in "
            "all warehouse areas at all times. Safety glasses are required in the "
            "cutting and packing zones. Cut-resistant gloves are required when "
            "handling banding or strapping.",
        ),
        (
            2,
            "Manual handling",
            "Loads above 20 kilograms require a two-person lift or a mechanical "
            "aid. Never lift above shoulder height without a platform.",
        ),
        (
            3,
            "Forklift operation",
            "Only certified operators may use a forklift. The pedestrian walkway "
            "must be kept clear at all times, and forklifts yield to pedestrians "
            "at every crossing.",
        ),
        (
            6,
            "Incident reporting",
            "All incidents and near-misses must be reported to the shift "
            "supervisor before the end of the shift, however minor.",
        ),
    ],
    # -----------------------------------------------------------------------
    # This one is deliberately left without a body: its ingestion job is seeded
    # as FAILED so the failure path is visible in the UI.
    "seed-doc-allhands": [],
}
