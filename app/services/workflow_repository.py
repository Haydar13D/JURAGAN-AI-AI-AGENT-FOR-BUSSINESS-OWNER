"""
app/services/workflow_repository.py
Persistent Database Repository & Audit Memory Engine for Kirata AI Workflow Builder.
Persists workflows, immutable versions, run execution logs, node input/output snapshots,
and human action approvals into SQLite/SQLAlchemy.
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy import select, update, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.workflow import (
    Tenant,
    WorkflowTemplate,
    Workflow,
    WorkflowVersion,
    WorkflowProposal,
    WorkflowRun,
    NodeRunEvent as NodeRunEventModel,
    ActionApproval as ActionApprovalModel,
    NodeCheckpoint,
)
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowStatus,
    NodeRunEvent,
    ActionApprovalRequest,
    RunStatus,
    BusinessProposal,
    ConfigPatchOperation,
    ProposalStatus,
)
from app.services import template_catalog


class WorkflowRepository:
    """
    Asynchronous repository for storing and querying workflow definitions,
    execution traces, input/output memory snapshots, and approval audit records.
    """

    @classmethod
    async def ensure_seed_data(cls, session: AsyncSession) -> None:
        """Seeds default tenants and templates if they do not exist."""
        # 1. Tenants
        default_tenants = [
            ("tenant_juragan_retail", "Juragan Retail iPhone"),
            ("tenant_fnb", "Juragan F&B Cafe & Resto"),
        ]
        for tid, tname in default_tenants:
            existing = await session.get(Tenant, tid)
            if not existing:
                session.add(Tenant(id=tid, name=tname, is_active=True))

        # 2. Workflow templates
        catalog = template_catalog.list_catalog()
        for tpl in catalog:
            t_id = tpl["id"]
            existing_tpl = await session.get(WorkflowTemplate, t_id)
            if not existing_tpl:
                def_data = template_catalog.load_template_definition(t_id) or {}
                session.add(
                    WorkflowTemplate(
                        id=t_id,
                        name=tpl.get("name", t_id),
                        category=tpl.get("industry", "GENERAL"),
                        description=tpl.get("business_outcome", ""),
                        version=tpl.get("version", "1.0.0"),
                        default_definition_json=json.dumps(def_data),
                        is_active=tpl.get("status") == "AVAILABLE",
                    )
                )

        await session.commit()

    @classmethod
    async def save_workflow(
        cls,
        session: AsyncSession,
        wf_def: WorkflowDefinition,
        is_active: bool = False,
    ) -> None:
        """Persists or updates workflow metadata and its version record."""
        await cls.ensure_seed_data(session)

        # Ensure tenant exists
        tenant = await session.get(Tenant, wf_def.tenant_id)
        if not tenant:
            tenant = Tenant(id=wf_def.tenant_id, name="Default Tenant", is_active=True)
            session.add(tenant)
            await session.flush()

        # Check or create workflow parent record
        wf_row = await session.get(Workflow, wf_def.id)
        if not wf_row:
            wf_row = Workflow(
                id=wf_def.id,
                tenant_id=wf_def.tenant_id,
                template_id=wf_def.template_id,
                name=wf_def.name,
                description=wf_def.description or "",
                active_version=wf_def.version if is_active else (1 if wf_def.version == 1 and is_active else 0),
                status=wf_def.status.value if hasattr(wf_def.status, "value") else str(wf_def.status),
            )
            session.add(wf_row)
        else:
            wf_row.name = wf_def.name
            wf_row.description = wf_def.description or ""
            if is_active:
                wf_row.active_version = wf_def.version
                wf_row.status = "ACTIVE"
            elif wf_def.status:
                wf_row.status = wf_def.status.value if hasattr(wf_def.status, "value") else str(wf_def.status)

        # Upsert WorkflowVersion
        version_id = f"{wf_def.id}_v{wf_def.version}"
        ver_row = await session.get(WorkflowVersion, version_id)
        def_json = wf_def.model_dump_json()

        if not ver_row:
            ver_row = WorkflowVersion(
                id=version_id,
                workflow_id=wf_def.id,
                version=wf_def.version,
                status=wf_def.status.value if hasattr(wf_def.status, "value") else str(wf_def.status),
                is_immutable=is_active,
                definition_json=def_json,
                activated_at=datetime.now() if is_active else None,
            )
            session.add(ver_row)
        else:
            ver_row.definition_json = def_json
            ver_row.status = wf_def.status.value if hasattr(wf_def.status, "value") else str(wf_def.status)
            if is_active:
                ver_row.is_immutable = True
                ver_row.activated_at = datetime.now()

        await session.commit()

    @classmethod
    async def get_workflow_version(
        cls,
        session: AsyncSession,
        workflow_id: str,
        version: int,
    ) -> Optional[WorkflowDefinition]:
        """Fetches a specific version definition from database."""
        version_id = f"{workflow_id}_v{version}"
        ver_row = await session.get(WorkflowVersion, version_id)
        if not ver_row:
            return None
        data = json.loads(ver_row.definition_json)
        return WorkflowDefinition.model_validate(data)

    @classmethod
    async def get_active_workflow(
        cls,
        session: AsyncSession,
        workflow_id: str,
    ) -> Optional[WorkflowDefinition]:
        """Fetches currently active version of a workflow."""
        wf_row = await session.get(Workflow, workflow_id)
        if not wf_row or not wf_row.active_version:
            return None
        return await cls.get_workflow_version(session, workflow_id, wf_row.active_version)

    @classmethod
    async def get_all_versions_dict(
        cls,
        session: AsyncSession,
        workflow_id: str,
    ) -> Dict[int, WorkflowDefinition]:
        """Returns all versions of a workflow as {version_no: WorkflowDefinition}."""
        stmt = (
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version.asc())
        )
        res = await session.execute(stmt)
        rows = res.scalars().all()
        result = {}
        for r in rows:
            data = json.loads(r.definition_json)
            result[r.version] = WorkflowDefinition.model_validate(data)
        return result

    @classmethod
    async def list_workflows(
        cls,
        session: AsyncSession,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Queries workflows list along with active version and run counts."""
        stmt = select(Workflow).order_by(Workflow.created_at.desc())
        if tenant_id:
            stmt = stmt.where(Workflow.tenant_id == tenant_id)

        res = await session.execute(stmt)
        workflows = res.scalars().all()

        results = []
        for w in workflows:
            ver_stmt = select(WorkflowVersion).where(WorkflowVersion.workflow_id == w.id).order_by(WorkflowVersion.version.asc())
            v_res = await session.execute(ver_stmt)
            versions = v_res.scalars().all()

            run_stmt = select(func.count(WorkflowRun.id)).where(WorkflowRun.workflow_id == w.id)
            rc_res = await session.execute(run_stmt)
            run_count = rc_res.scalar_one() or 0

            latest_v = max([v.version for v in versions]) if versions else 1

            results.append({
                "id": w.id,
                "name": w.name,
                "template_id": w.template_id,
                "tenant_id": w.tenant_id,
                "active_version": w.active_version if w.active_version and w.active_version > 0 else None,
                "latest_version": latest_v,
                "versions": [{"version": v.version, "status": v.status} for v in versions],
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "run_count": run_count,
            })
        return results

    @classmethod
    async def save_run_record(
        cls,
        session: AsyncSession,
        run_id: str,
        workflow_id: str,
        tenant_id: str,
        version: int,
        run_mode: str,
        status: str,
        traces: List[NodeRunEvent],
        pending_action: Optional[ActionApprovalRequest] = None,
        output: Optional[Any] = None,
        csv_content: Optional[str] = None,
        approved_by: Optional[str] = None,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        """Persists or updates a workflow run and all its execution memory snapshots."""
        await cls.ensure_seed_data(session)

        output_json = None
        if output is not None:
            if hasattr(output, "model_dump"):
                output_json = json.dumps(output.model_dump())
            elif isinstance(output, (dict, list)):
                output_json = json.dumps(output)
            else:
                output_json = json.dumps({"raw": str(output)})

        run_row = await session.get(WorkflowRun, run_id)
        if not run_row:
            run_row = WorkflowRun(
                id=run_id,
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                workflow_version=version,
                run_mode=run_mode,
                status=status,
                summary_json=output_json,
                started_at=started_at or datetime.now(),
                completed_at=completed_at,
            )
            session.add(run_row)
        else:
            run_row.status = status
            if completed_at:
                run_row.completed_at = completed_at
            if output_json:
                run_row.summary_json = output_json

        await session.flush()

        # Delete existing node events for idempotency
        stmt_del = select(NodeRunEventModel).where(NodeRunEventModel.run_id == run_id)
        res_del = await session.execute(stmt_del)
        for old_ev in res_del.scalars().all():
            await session.delete(old_ev)

        # Insert new NodeRunEvents with input/output snapshots
        for trace in traces:
            s_time = datetime.fromisoformat(trace.started_at) if trace.started_at else datetime.now()
            c_time = datetime.fromisoformat(trace.completed_at) if trace.completed_at else None

            session.add(
                NodeRunEventModel(
                    run_id=run_id,
                    node_id=trace.node_id,
                    tool_type=trace.tool_type,
                    status=trace.status.value if hasattr(trace.status, "value") else str(trace.status),
                    started_at=s_time,
                    completed_at=c_time,
                    input_snapshot_json=json.dumps(trace.input_snapshot) if trace.input_snapshot else None,
                    output_snapshot_json=json.dumps(trace.output_snapshot) if trace.output_snapshot else None,
                    error_message=trace.error_message,
                    execution_time_ms=trace.execution_time_ms,
                )
            )

        # Handle ActionApproval
        if pending_action:
            appr_row = await session.get(ActionApprovalModel, pending_action.action_id)
            if not appr_row:
                session.add(
                    ActionApprovalModel(
                        id=pending_action.action_id,
                        run_id=run_id,
                        node_id=pending_action.node_id,
                        action_type=pending_action.action_type,
                        preview_payload_json=json.dumps(pending_action.preview_data),
                        status="PENDING",
                        created_at=datetime.now(),
                    )
                )

        await session.commit()

    @classmethod
    async def record_action_decision(
        cls,
        session: AsyncSession,
        run_id: str,
        action_id: str,
        decision: str,
        reviewer_id: str,
        decision_reason: Optional[str] = None,
        final_output: Optional[Any] = None,
        updated_traces: Optional[List[NodeRunEvent]] = None,
    ) -> None:
        """Updates action approval with human decision and marks run completed/rejected."""
        appr_row = await session.get(ActionApprovalModel, action_id)
        if appr_row:
            appr_row.status = decision
            appr_row.reviewed_by = reviewer_id
            appr_row.decision_reason = decision_reason
            appr_row.reviewed_at = datetime.now()

        run_row = await session.get(WorkflowRun, run_id)
        if run_row:
            run_row.status = "COMPLETED" if decision == "APPROVE" else "REJECTED"
            run_row.completed_at = datetime.now()
            if final_output:
                if hasattr(final_output, "model_dump"):
                    run_row.summary_json = json.dumps(final_output.model_dump())
                elif isinstance(final_output, (dict, list)):
                    run_row.summary_json = json.dumps(final_output)

        if updated_traces:
            for trace in updated_traces:
                # Add any nodes executed post-approval
                s_time = datetime.fromisoformat(trace.started_at) if trace.started_at else datetime.now()
                c_time = datetime.fromisoformat(trace.completed_at) if trace.completed_at else None
                session.add(
                    NodeRunEventModel(
                        run_id=run_id,
                        node_id=trace.node_id,
                        tool_type=trace.tool_type,
                        status=trace.status.value if hasattr(trace.status, "value") else str(trace.status),
                        started_at=s_time,
                        completed_at=c_time,
                        input_snapshot_json=json.dumps(trace.input_snapshot) if trace.input_snapshot else None,
                        output_snapshot_json=json.dumps(trace.output_snapshot) if trace.output_snapshot else None,
                        error_message=trace.error_message,
                        execution_time_ms=trace.execution_time_ms,
                    )
                )

        await session.commit()

    @classmethod
    async def list_runs(
        cls,
        session: AsyncSession,
        tenant_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        status: Optional[str] = None,
        run_mode: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Queries historical workflow runs from database with workflow names."""
        stmt = (
            select(WorkflowRun, Workflow.name.label("workflow_name"))
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id, isouter=True)
            .order_by(WorkflowRun.started_at.desc())
            .limit(limit)
        )

        if tenant_id:
            stmt = stmt.where(WorkflowRun.tenant_id == tenant_id)
        if workflow_id:
            stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
        if status:
            stmt = stmt.where(WorkflowRun.status == status)
        if run_mode:
            stmt = stmt.where(WorkflowRun.run_mode == run_mode)

        res = await session.execute(stmt)
        rows = res.all()

        results = []
        for r, wf_name in rows:
            results.append({
                "run_id": r.id,
                "workflow_id": r.workflow_id,
                "workflow_name": wf_name or r.workflow_id,
                "version": r.workflow_version,
                "run_mode": r.run_mode,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            })
        return results

    @classmethod
    async def get_run_detail(
        cls,
        session: AsyncSession,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetches complete execution memory, traces, snapshots, and approval audit for a run."""
        stmt = (
            select(WorkflowRun, Workflow.name.label("workflow_name"))
            .join(Workflow, Workflow.id == WorkflowRun.workflow_id, isouter=True)
            .where(WorkflowRun.id == run_id)
        )
        res = await session.execute(stmt)
        row = res.first()
        if not row:
            return None

        r, wf_name = row

        # Fetch traces with input & output snapshots
        ev_stmt = (
            select(NodeRunEventModel)
            .where(NodeRunEventModel.run_id == run_id)
            .order_by(NodeRunEventModel.id.asc())
        )
        ev_res = await session.execute(ev_stmt)
        events = ev_res.scalars().all()

        traces = []
        for ev in events:
            in_snap = json.loads(ev.input_snapshot_json) if ev.input_snapshot_json else None
            out_snap = json.loads(ev.output_snapshot_json) if ev.output_snapshot_json else None
            traces.append({
                "node_id": ev.node_id,
                "tool_type": ev.tool_type,
                "status": ev.status,
                "started_at": ev.started_at.isoformat() if ev.started_at else None,
                "completed_at": ev.completed_at.isoformat() if ev.completed_at else None,
                "input_snapshot": in_snap,
                "output_snapshot": out_snap,
                "error_message": ev.error_message,
                "execution_time_ms": ev.execution_time_ms,
            })

        # Fetch approval records
        appr_stmt = select(ActionApprovalModel).where(ActionApprovalModel.run_id == run_id)
        appr_res = await session.execute(appr_stmt)
        approvals = appr_res.scalars().all()

        pending_action = None
        action_history = []
        for a in approvals:
            item = {
                "action_id": a.id,
                "node_id": a.node_id,
                "action_type": a.action_type,
                "status": a.status,
                "reviewed_by": a.reviewed_by,
                "decision_reason": a.decision_reason,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
                "preview_data": json.loads(a.preview_payload_json) if a.preview_payload_json else {},
            }
            action_history.append(item)
            if a.status == "PENDING":
                pending_action = item

        out_data = json.loads(r.summary_json) if r.summary_json else None

        return {
            "run_id": r.id,
            "workflow_id": r.workflow_id,
            "workflow_name": wf_name or r.workflow_id,
            "version": r.workflow_version,
            "run_mode": r.run_mode,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "traces": traces,
            "pending_action": pending_action,
            "action_history": action_history,
            "output": out_data,
        }

    @classmethod
    async def save_node_checkpoint(
        cls,
        session: AsyncSession,
        run_id: str,
        node_id: str,
        tool_type: str,
        output_data: Any,
    ) -> None:
        """Menyimpan checkpoint output node yang berstatus COMPLETED secara idempoten."""
        if hasattr(output_data, "model_dump"):
            serialized = json.dumps(output_data.model_dump(), ensure_ascii=False, default=str)
        elif isinstance(output_data, (dict, list, str, int, float, bool)) or output_data is None:
            serialized = json.dumps(output_data, ensure_ascii=False, default=str)
        else:
            serialized = json.dumps(str(output_data), ensure_ascii=False)

        ckpt_id = f"{run_id}:{node_id}"
        existing = await session.get(NodeCheckpoint, ckpt_id)
        if existing:
            existing.tool_type = tool_type
            existing.status = "COMPLETED"
            existing.output_json = serialized
        else:
            session.add(
                NodeCheckpoint(
                    id=ckpt_id,
                    run_id=run_id,
                    node_id=node_id,
                    tool_type=tool_type,
                    status="COMPLETED",
                    output_json=serialized,
                )
            )
        await session.commit()

    @classmethod
    async def load_run_checkpoint(
        cls,
        session: AsyncSession,
        run_id: str,
    ) -> Dict[str, Any]:
        """Memuat dictionary {node_id: output_data} untuk seluruh node COMPLETED pada run_id."""
        stmt = select(NodeCheckpoint).where(
            NodeCheckpoint.run_id == run_id,
            NodeCheckpoint.status == "COMPLETED",
        )
        res = await session.execute(stmt)
        rows = res.scalars().all()
        checkpoints: Dict[str, Any] = {}
        for row in rows:
            checkpoints[row.node_id] = json.loads(row.output_json)
        return checkpoints

    @classmethod
    async def save_business_proposal(
        cls,
        session: AsyncSession,
        proposal: BusinessProposal,
    ) -> None:
        """Menyimpan atau memperbarui BusinessProposal ke tabel workflow_proposals."""
        meta_payload = {
            "kind": "business_proposal",
            "intent": proposal.intent,
            "changes": [c.model_dump() for c in proposal.changes],
            "candidate_config": proposal.candidate_config,
            "created_at": proposal.created_at,
            "reviewed_at": proposal.reviewed_at,
            "reviewed_by": proposal.reviewed_by,
        }
        existing = await session.get(WorkflowProposal, proposal.proposal_id)
        if existing:
            existing.base_version = proposal.base_version
            existing.proposed_version = proposal.proposed_version
            existing.rationale = proposal.explanation
            existing.patch_operations_json = json.dumps(meta_payload, ensure_ascii=False)
            existing.diff_json = json.dumps(proposal.diff_summary, ensure_ascii=False)
            existing.is_valid = proposal.is_valid
            existing.validation_errors_json = json.dumps(proposal.validation_errors, ensure_ascii=False)
            existing.status = proposal.status.value
        else:
            session.add(
                WorkflowProposal(
                    id=proposal.proposal_id,
                    workflow_id=proposal.workflow_id,
                    base_version=proposal.base_version,
                    proposed_version=proposal.proposed_version,
                    rationale=proposal.explanation,
                    risk_level="LOW",
                    patch_operations_json=json.dumps(meta_payload, ensure_ascii=False),
                    diff_json=json.dumps(proposal.diff_summary, ensure_ascii=False),
                    is_valid=proposal.is_valid,
                    validation_errors_json=json.dumps(proposal.validation_errors, ensure_ascii=False),
                    status=proposal.status.value,
                )
            )
        await session.commit()

    @classmethod
    async def get_business_proposal(
        cls,
        session: AsyncSession,
        proposal_id: str,
    ) -> Optional[BusinessProposal]:
        """Memuat BusinessProposal dari tabel workflow_proposals."""
        row = await session.get(WorkflowProposal, proposal_id)
        if not row:
            return None
        meta = json.loads(row.patch_operations_json) if row.patch_operations_json else {}
        if not isinstance(meta, dict) or meta.get("kind") != "business_proposal":
            return None
        changes = [ConfigPatchOperation.model_validate(c) for c in meta.get("changes", [])]
        diff_summary = json.loads(row.diff_json) if row.diff_json else {}
        val_errors = json.loads(row.validation_errors_json) if row.validation_errors_json else []
        created_at = meta.get("created_at") or (row.created_at.isoformat() if row.created_at else datetime.now().isoformat())
        return BusinessProposal(
            proposal_id=row.id,
            intent=meta.get("intent", "MODIFY_WORKFLOW"),
            workflow_id=row.workflow_id,
            base_version=row.base_version,
            proposed_version=row.proposed_version,
            changes=changes,
            explanation=row.rationale,
            diff_summary=diff_summary,
            is_valid=row.is_valid,
            validation_errors=val_errors,
            status=ProposalStatus(row.status),
            created_at=created_at,
            reviewed_at=meta.get("reviewed_at"),
            reviewed_by=meta.get("reviewed_by"),
            candidate_config=meta.get("candidate_config"),
        )

    @classmethod
    async def save_patch_proposal(
        cls,
        session: AsyncSession,
        proposal_id: str,
        workflow_id: str,
        proposal_data: Dict[str, Any],
    ) -> None:
        """Menyimpan PatchProposal (dari /workflows/{id}/proposals) ke workflow_proposals."""
        existing = await session.get(WorkflowProposal, proposal_id)
        payload = {"kind": "patch_proposal", "proposal": proposal_data}
        if existing:
            existing.status = str(proposal_data.get("status", "PENDING"))
            existing.patch_operations_json = json.dumps(payload, ensure_ascii=False)
        else:
            session.add(
                WorkflowProposal(
                    id=proposal_id,
                    workflow_id=workflow_id,
                    base_version=int(proposal_data.get("base_version", 1)),
                    proposed_version=int(proposal_data.get("proposed_version", 2)),
                    rationale=str(proposal_data.get("rationale", "")),
                    risk_level=str(proposal_data.get("risk_level", "LOW")),
                    patch_operations_json=json.dumps(payload, ensure_ascii=False),
                    diff_json=json.dumps(proposal_data.get("diff", {}), ensure_ascii=False),
                    is_valid=bool(proposal_data.get("is_valid", True)),
                    validation_errors_json=json.dumps(proposal_data.get("validation_errors", []), ensure_ascii=False),
                    status=str(proposal_data.get("status", "PENDING")),
                )
            )
        await session.commit()

    @classmethod
    async def get_patch_proposal(
        cls,
        session: AsyncSession,
        proposal_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Memuat PatchProposal dari tabel workflow_proposals."""
        row = await session.get(WorkflowProposal, proposal_id)
        if not row:
            return None
        meta = json.loads(row.patch_operations_json) if row.patch_operations_json else {}
        if isinstance(meta, dict) and meta.get("kind") == "patch_proposal":
            return {"workflow_id": row.workflow_id, "proposal": meta.get("proposal", {})}
        return None

    @classmethod
    async def get_pending_action(
        cls,
        session: AsyncSession,
        action_id: str,
    ) -> Optional[ActionApprovalModel]:
        """Mengambil record ActionApproval berdasarkan action_id."""
        return await session.get(ActionApprovalModel, action_id)

    @classmethod
    async def get_workflow_summary(
        cls,
        session: AsyncSession,
        workflow_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Mengambil ringkasan satu workflow dari database."""
        w = await session.get(Workflow, workflow_id)
        if not w:
            return None
        ver_stmt = select(WorkflowVersion).where(WorkflowVersion.workflow_id == w.id).order_by(WorkflowVersion.version.asc())
        v_res = await session.execute(ver_stmt)
        versions = v_res.scalars().all()
        run_stmt = select(func.count(WorkflowRun.id)).where(WorkflowRun.workflow_id == w.id)
        rc_res = await session.execute(run_stmt)
        run_count = rc_res.scalar_one() or 0
        latest_v = max([v.version for v in versions]) if versions else 1
        return {
            "id": w.id,
            "name": w.name,
            "template_id": w.template_id,
            "tenant_id": w.tenant_id,
            "active_version": w.active_version if w.active_version and w.active_version > 0 else None,
            "latest_version": latest_v,
            "versions": [{"version": v.version, "status": v.status} for v in versions],
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "run_count": run_count,
        }

    @classmethod
    async def delete_draft_version(
        cls,
        session: AsyncSession,
        workflow_id: str,
        version: int,
    ) -> None:
        """Menghapus versi DRAFT dari tabel workflow_versions."""
        ver_id = f"{workflow_id}_v{version}"
        row = await session.get(WorkflowVersion, ver_id)
        if row and row.status == "DRAFT":
            await session.delete(row)
            await session.commit()
