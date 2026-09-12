"""Seed demo data so dashboards are never empty.

Idempotent: re-running upserts by stable seed IDs rather than duplicating.

Two tenants are created deliberately, so cross-tenant isolation is
demonstrable in the UI and in E2E tests.

Documents are ingested through the REAL pipeline, so chunks, embeddings and
citations are genuine - never fabricated rows.

    python -m seeds.seed
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.context import PLATFORM_TENANT_ID, RequestContext
from app.core.logging import configure_logging, get_logger
from app.db.models import (
    AuditEvent,
    Conversation,
    Department,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Message,
    Modality,
    PromptRelease,
    RequestUsage,
    Tenant,
    User,
    UserFeedback,
    UserRole,
)
from app.db.session import dispose_engine, get_sessionmaker
from app.services.rag.prompts import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_NAME,
    SYSTEM_PROMPT_VERSION,
)
from seeds.documents import SEED_DOCUMENT_BODIES

logger = get_logger("seed")

# Stable IDs make the seed idempotent.
TENANT_A = "seed-tenant-northwind"
TENANT_B = "seed-tenant-contoso"

# The service provider's own tenant, created by migration 0002. Seeding an
# operator into it is what makes the onboarding flow demonstrable locally: a
# platform admin has to exist before it can create the first company, and on
# AWS that identity is created out-of-band by scripts/bootstrap-platform-admin.sh.
PLATFORM_TENANT = PLATFORM_TENANT_ID
PLATFORM_ADMIN_ID = "seed-platform-admin"

SEED_DOCUMENTS = [
    (
        "seed-doc-travel-2026",
        "Travel Policy 2026.pdf",
        Department.FINANCE,
        Modality.TEXT,
        12,
        48,
    ),
    (
        "seed-doc-travel-2024",
        "Travel Policy 2024 (superseded).pdf",
        Department.FINANCE,
        Modality.TEXT,
        10,
        41,
    ),
    ("seed-doc-onboarding", "Employee Onboarding SOP.docx", Department.HR, Modality.TEXT, 8, 32),
    ("seed-doc-leave", "Leave and Absence Policy.pdf", Department.HR, Modality.TEXT, 6, 24),
    ("seed-doc-nda", "Standard NDA Template.docx", Department.LEGAL, Modality.TEXT, 4, 16),
    ("seed-doc-q3-spend", "Q3 Departmental Spend.xlsx", Department.FINANCE, Modality.TABLE, 1, 22),
    (
        "seed-doc-arch",
        "Platform Architecture Diagram.png",
        Department.TECHNICAL,
        Modality.IMAGE,
        1,
        3,
    ),
    (
        "seed-doc-security",
        "Security Incident Runbook.md",
        Department.TECHNICAL,
        Modality.TEXT,
        5,
        19,
    ),
    (
        "seed-doc-pitch",
        "Q4 Sales Enablement Deck.pdf",
        Department.SALES,
        Modality.TEXT,
        18,
        54,
    ),
    ("seed-doc-brand", "Brand Voice Guidelines.pdf", Department.MARKETING, Modality.TEXT, 7, 28),
    (
        "seed-doc-warehouse",
        "Warehouse Safety SOP.pdf",
        Department.OPERATIONS,
        Modality.TEXT,
        9,
        35,
    ),
    (
        "seed-doc-allhands",
        "All-Hands Recording (Sept).mp4",
        Department.OPERATIONS,
        Modality.VIDEO,
        None,
        27,
    ),
]

SEED_QUESTIONS = [
    ("What is the domestic hotel reimbursement limit?", 0.87, Department.FINANCE),
    ("How do I onboard a new contractor?", 0.79, Department.HR),
    ("What changed between the 2024 and 2026 travel policies?", 0.82, Department.FINANCE),
    ("What is our incident escalation path?", 0.91, Department.TECHNICAL),
    ("How much leave carries over to next year?", 0.74, Department.HR),
    ("Which department overspent in Q3?", 0.68, Department.FINANCE),
    ("What is the standard NDA term length?", 0.85, Department.LEGAL),
    ("What PPE is required in the warehouse?", 0.88, Department.OPERATIONS),
]


async def _upsert_tenant(session, tenant_id: str, name: str, slug: str) -> Tenant:  # type: ignore[no-untyped-def]
    existing = await session.get(Tenant, tenant_id)
    if existing:
        return existing
    tenant = Tenant(id=tenant_id, name=name, slug=slug)
    session.add(tenant)
    await session.flush()
    return tenant


async def _upsert_user(  # type: ignore[no-untyped-def]
    session,
    user_id: str,
    tenant_id: str,
    email: str,
    role: UserRole,
    name: str,
    department: Department | None,
) -> User:
    existing = await session.get(User, user_id)
    if existing:
        return existing
    user = User(
        id=user_id,
        cognito_sub=user_id,
        tenant_id=tenant_id,
        email=email,
        display_name=name,
        role=role,
        department=department,
        last_login_at=datetime.now(UTC) - timedelta(hours=3),
    )
    session.add(user)
    await session.flush()
    return user


async def seed() -> None:
    configure_logging()
    now = datetime.now(UTC)

    async with get_sessionmaker()() as session:
        # -- tenants -----------------------------------------------------
        # The platform tenant already exists (migration 0002); upserting it
        # keeps the seed runnable against a database restored from an older
        # snapshot without assuming migration order.
        await _upsert_tenant(session, PLATFORM_TENANT, "Platform Operations", PLATFORM_TENANT_ID)
        await _upsert_tenant(session, TENANT_A, "Northwind Industries", "northwind")
        await _upsert_tenant(session, TENANT_B, "Contoso Ltd", "contoso")

        # -- the service provider's operator -----------------------------
        await _upsert_user(
            session,
            PLATFORM_ADMIN_ID,
            PLATFORM_TENANT,
            "platform@ekba.example",
            UserRole.PLATFORM_ADMIN,
            "Platform Operator",
            None,
        )

        # -- users -------------------------------------------------------
        await _upsert_user(
            session,
            "seed-user-a",
            TENANT_A,
            "priya@northwind.example",
            UserRole.USER,
            "Priya Raman",
            Department.FINANCE,
        )
        await _upsert_user(
            session,
            "seed-admin-a",
            TENANT_A,
            "admin@northwind.example",
            UserRole.ADMIN,
            "Sam Okafor",
            Department.TECHNICAL,
        )
        # Tenant B exists so isolation is visible and testable.
        await _upsert_user(
            session,
            "seed-user-b",
            TENANT_B,
            "jordan@contoso.example",
            UserRole.USER,
            "Jordan Blake",
            Department.SALES,
        )
        await _upsert_user(
            session,
            "seed-admin-b",
            TENANT_B,
            "admin@contoso.example",
            UserRole.ADMIN,
            "Alex Winter",
            Department.TECHNICAL,
        )

        ctx = RequestContext(
            user_id="seed-user-a",
            tenant_id=TENANT_A,
            role="user",
            email="priya@northwind.example",
        )

        # -- documents + ingestion jobs ----------------------------------
        for index, (doc_id, name, dept, modality, pages, chunks) in enumerate(SEED_DOCUMENTS):
            if await session.get(Document, doc_id):
                continue

            # One deliberately failed job so the failure path is visible in the UI.
            failed = index == len(SEED_DOCUMENTS) - 1
            created = now - timedelta(days=len(SEED_DOCUMENTS) - index, hours=index)

            session.add(
                Document(
                    id=doc_id,
                    tenant_id=TENANT_A,
                    owner_id="seed-user-a",
                    created_by="seed-user-a",
                    name=name,
                    original_filename=name,
                    content_type="application/pdf",
                    modality=modality,
                    department=dept,
                    source_uri=f"s3://seed/{TENANT_A}/{doc_id}",
                    size_bytes=180_000 + index * 24_000,
                    status=DocumentStatus.FAILED if failed else DocumentStatus.READY,
                    chunk_count=0 if failed else chunks,
                    page_count=pages,
                    created_at=created,
                    updated_at=created,
                )
            )
            session.add(
                IngestionJob(
                    id=f"job-{doc_id}",
                    tenant_id=TENANT_A,
                    document_id=doc_id,
                    created_by="seed-user-a",
                    status=JobStatus.FAILED if failed else JobStatus.COMPLETED,
                    progress=35 if failed else 100,
                    chunks_written=0 if failed else chunks,
                    error_code="TranscriptionTimeout" if failed else None,
                    error_message=(
                        "Amazon Transcribe did not finish within the 15 minute ceiling."
                        if failed
                        else None
                    ),
                    finished_at=created + timedelta(minutes=2),
                    created_at=created,
                    updated_at=created,
                )
            )

        # -- conversations, messages, usage ------------------------------
        existing_convs = (
            (
                await session.execute(
                    select(Conversation.id).where(Conversation.tenant_id == TENANT_A)
                )
            )
            .scalars()
            .all()
        )

        if not existing_convs:
            for index, (question, confidence, dept) in enumerate(SEED_QUESTIONS):
                asked = now - timedelta(hours=index * 5 + 1)
                conversation = Conversation(
                    id=f"seed-conv-{index}",
                    tenant_id=TENANT_A,
                    user_id="seed-user-a",
                    title=question[:200],
                    department=dept,
                    created_at=asked,
                    updated_at=asked,
                )
                session.add(conversation)

                citations = [
                    {
                        "source_number": 1,
                        "document_id": SEED_DOCUMENTS[index % len(SEED_DOCUMENTS)][0],
                        "document_name": SEED_DOCUMENTS[index % len(SEED_DOCUMENTS)][1],
                        "chunk_id": f"seed-chunk-{index}",
                        "page_number": 3 + index % 5,
                        "score": round(confidence, 3),
                    }
                ]
                session.add(
                    Message(
                        id=f"seed-msg-u-{index}",
                        tenant_id=TENANT_A,
                        conversation_id=conversation.id,
                        role="user",
                        content=question,
                        created_at=asked,
                        updated_at=asked,
                    )
                )
                session.add(
                    Message(
                        id=f"seed-msg-a-{index}",
                        tenant_id=TENANT_A,
                        conversation_id=conversation.id,
                        role="assistant",
                        content=f"Based on the retrieved policy documents [S1]. ({question})",
                        citations=citations,
                        model_used="amazon.nova-lite-v1:0",
                        confidence=confidence,
                        cache_hit=index % 4 == 0,
                        created_at=asked,
                        updated_at=asked,
                    )
                )
                session.add(
                    RequestUsage(
                        id=f"seed-usage-{index}",
                        tenant_id=TENANT_A,
                        user_id="seed-user-a",
                        operation="chat",
                        model_used="amazon.nova-lite-v1:0",
                        input_tokens=1400 + index * 180,
                        output_tokens=180 + index * 22,
                        estimated_cost=round(0.00012 + index * 0.00002, 8),
                        latency_ms=680 + index * 95,
                        cache_hit=index % 4 == 0,
                        created_at=asked,
                        updated_at=asked,
                    )
                )

            # feedback so the Feedback page renders
            for index, rating in enumerate([1, 1, -1, 1, 1]):
                session.add(
                    UserFeedback(
                        id=f"seed-fb-{index}",
                        tenant_id=TENANT_A,
                        user_id="seed-user-a",
                        message_id=f"seed-msg-a-{index}",
                        rating=rating,
                        reason=None if rating > 0 else "missing_citation",
                        comment=None if rating > 0 else "The cited page did not contain this.",
                    )
                )

        # -- audit events so Audit Logs and Security render ---------------
        existing_audit = (
            await session.execute(
                select(AuditEvent.id).where(AuditEvent.tenant_id == TENANT_A).limit(1)
            )
        ).first()

        if not existing_audit:
            events = [
                ("auth.login", "info", "successful_login", None),
                ("document.uploaded", "info", "modality_text", "seed-doc-travel-2026"),
                ("document.deleted", "info", "owner_or_admin_authorized", "seed-doc-old"),
                ("ratelimit.exceeded", "warning", "bucket_api_limit_20", None),
                ("guardrail.input_injection_detected", "error", "instruction_override", None),
                ("guardrail.invented_citation", "warning", "unmapped_source_numbers_1", None),
                (
                    "tenant.cross_tenant_document_access",
                    "critical",
                    "document_belongs_to_another_tenant",
                    "seed-doc-foreign",
                ),
                ("authz.admin_required", "error", "non_admin_attempted_admin_endpoint", None),
                ("ingestion.failed", "warning", "TranscriptionTimeout", "seed-doc-allhands"),
            ]
            for index, (event_type, severity, reason, resource) in enumerate(events):
                occurred = now - timedelta(hours=index * 3 + 2)
                session.add(
                    AuditEvent(
                        id=f"seed-audit-{index}",
                        tenant_id=TENANT_A,
                        user_id="seed-user-a",
                        event_type=event_type,
                        severity=severity,
                        resource_type="document" if resource else None,
                        resource_id=resource,
                        reason=reason,
                        created_at=occurred,
                        updated_at=occurred,
                    )
                )

        # -- prompt release ----------------------------------------------
        existing_prompt = (
            await session.execute(
                select(PromptRelease.id).where(
                    PromptRelease.name == SYSTEM_PROMPT_NAME,
                    PromptRelease.version == SYSTEM_PROMPT_VERSION,
                )
            )
        ).first()
        if not existing_prompt:
            session.add(
                PromptRelease(
                    id="seed-prompt-1",
                    name=SYSTEM_PROMPT_NAME,
                    version=SYSTEM_PROMPT_VERSION,
                    content=SYSTEM_PROMPT,
                    is_active=True,
                    released_by="seed",
                    notes="Initial RAG answering prompt.",
                )
            )

        await session.commit()

        # ---- index into Qdrant --------------------------------------------
        # Without this the dashboards look populated but every question returns
        # "not found", because retrieval has nothing to search.
        indexed = await index_seed_documents(session, ctx)

        logger.info(
            "seed_completed",
            extra={
                "extra": {
                    "tenants": 2,
                    "users": 4,
                    "documents": len(SEED_DOCUMENTS),
                    "conversations": len(SEED_QUESTIONS),
                    "chunks_indexed": indexed,
                    "seeded_tenant": ctx.tenant_id,
                }
            },
        )

    await dispose_engine()


async def index_seed_documents(session, ctx: RequestContext) -> int:  # type: ignore[no-untyped-def]
    """Chunk, embed and upsert the seed bodies through the real pipeline.

    Uses the same chunker, the same embedding provider and the same Qdrant
    payload contract as a genuine upload, so citations and confidence in the
    demo are real rather than fabricated.

    Idempotent: the tenant's existing points are removed first, so re-running
    the seed does not accumulate duplicates.
    """
    from app.services import vector
    from app.services.ai.provider import get_provider
    from app.services.ingestion.chunker import chunk_blocks
    from app.services.ingestion.extractors import ExtractedBlock
    from app.services.security.injection import scan_content

    await vector.ensure_collection()

    provider = get_provider()
    created_at = datetime.now(UTC).isoformat()
    total = 0

    for doc_id, name, dept, modality, _pages, _claimed in SEED_DOCUMENTS:
        bodies = SEED_DOCUMENT_BODIES.get(doc_id, [])
        if not bodies:
            continue  # the deliberately-failed ingestion has no content

        document = await session.get(Document, doc_id)
        if document is None:
            continue

        # Re-running the seed must not duplicate points.
        await vector.delete_document_chunks(ctx, doc_id)

        blocks = [
            ExtractedBlock(text=body, page_number=page, section=section, modality=modality.value)
            for page, section, body in bodies
        ]
        chunks = chunk_blocks(blocks)
        if not chunks:
            continue

        result = await provider.embed([c.text for c in chunks])

        payloads = [
            vector.ChunkPayload(
                document_id=doc_id,
                chunk_id=str(uuid.uuid4()),
                document_name=name,
                page_number=chunk.page_number,
                source_uri=document.source_uri,
                owner_id=document.owner_id,
                tenant_id=ctx.tenant_id,  # mandatory
                document_version=document.version,
                created_by=document.created_by,
                created_at=created_at,
                text=chunk.text,
                modality=chunk.modality,
                department=dept.value,
                section=chunk.section,
                # Same poisoning scan a real upload gets.
                suspicious=scan_content(chunk.text).is_blocking,
            )
            for chunk in chunks
        ]

        total += await vector.upsert_chunks(result.vectors, payloads)

        # Report the count that is actually searchable, not a made-up number.
        document.chunk_count = len(chunks)
        document.doc_metadata = {
            **(document.doc_metadata or {}),
            "embedding_model": result.model_used,
            "embedding_dimension": result.dimension,
        }

    await session.commit()
    return total


if __name__ == "__main__":
    asyncio.run(seed())
