"""
app/services/workflow_runner.py
Deterministic Workflow Execution & Dry-Run Engine for Kirata AI Workflow Builder.
Executes workflows in topological DAG order, captures node run events,
and enforces two-tier human approvals.
"""

import time
import uuid
from datetime import datetime
from collections import defaultdict, deque
from typing import Dict, List, Any, Optional, Tuple

from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowNode,
    DryRunResult,
    NodeRunEvent,
    RunStatus,
    ActionApprovalRequest
)
from app.core.tool_registry import ToolRegistry, ExecutionContext


class WorkflowRunner:
    """
    Executes compiled canonical workflow definitions with support for isolated dry-runs and live runs.
    """

    @classmethod
    async def run_dry_run(
        cls,
        workflow: WorkflowDefinition,
        custom_csv_content: Optional[str] = None
    ) -> DryRunResult:
        """
        Runs an isolated, zero-side-effect dry-run using synthetic data fixture.
        """
        run_id = f"dry_run_{uuid.uuid4().hex[:8]}"
        context = ExecutionContext(
            tenant_id=workflow.tenant_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            run_id=run_id,
            is_dry_run=True,
            actor_id="dry_run_simulator"
        )

        topological_nodes = cls._get_topological_order(workflow)
        node_outputs: Dict[str, Any] = {}
        node_traces: List[NodeRunEvent] = []

        # If custom CSV provided, seed it into upload_vendor_list
        if custom_csv_content:
            context.extra_meta["custom_csv_content"] = custom_csv_content

        items_processed = 0
        items_flagged = 0

        for node in topological_nodes:
            tool = ToolRegistry.get(node.tool_type, node.tool_version)
            if not tool:
                raise ValueError(f"Tool '{node.tool_type}' not found in registry during dry-run.")

            # Resolve input bindings
            resolved_inputs = cls._resolve_input_bindings(node, node_outputs, context)

            start_time = datetime.now()
            t0 = time.perf_counter()
            error_msg = None
            out_snapshot = None
            status = RunStatus.COMPLETED

            try:
                # Validate inputs and config
                cfg_model = tool.validate_config(node.config)
                in_model = tool.validate_inputs(resolved_inputs)

                # Execute tool
                out_result = await tool.execute(cfg_model, in_model, context)
                node_outputs[node.id] = out_result

                # Metrics capture for catalog nodes
                if hasattr(out_result, "total_rows"):
                    items_processed = getattr(out_result, "total_rows")
                if hasattr(out_result, "flagged_items_count"):
                    items_flagged = getattr(out_result, "flagged_items_count")

                out_snapshot = cls._serialize_snapshot(out_result)
            except Exception as exc:
                status = RunStatus.FAILED
                error_msg = str(exc)

            t1 = time.perf_counter()
            end_time = datetime.now()

            trace = NodeRunEvent(
                node_id=node.id,
                tool_type=node.tool_type,
                status=status,
                started_at=start_time.isoformat(),
                completed_at=end_time.isoformat(),
                input_snapshot=cls._serialize_snapshot(resolved_inputs),
                output_snapshot=out_snapshot if status == RunStatus.COMPLETED else None,
                error_message=error_msg,
                execution_time_ms=round((t1 - t0) * 1000, 2)
            )
            node_traces.append(trace)

            if status == RunStatus.FAILED:
                break

        is_clean = all(t.status == RunStatus.COMPLETED for t in node_traces)
        summary = (
            f"Dry run selesai sukses: {len(node_traces)} node dieksekusi. "
            f"Total {items_processed} produk diproses ({items_flagged} memerlukan review owner)."
            if is_clean else
            f"Dry run gagal pada node '{node_traces[-1].node_id}': {node_traces[-1].error_message}"
        )

        return DryRunResult(
            workflow_id=workflow.id,
            version=workflow.version,
            is_clean=is_clean,
            node_traces=node_traces,
            items_processed=items_processed,
            items_flagged_for_review=items_flagged,
            estimated_api_cost_usd=0.002,
            summary=summary
        )

    @classmethod
    async def run_live(
        cls,
        workflow: WorkflowDefinition,
        run_id: str,
        csv_content: Optional[str] = None,
        action_approval_granted: bool = False,
        reviewer_id: Optional[str] = None
    ) -> Tuple[RunStatus, List[NodeRunEvent], Optional[ActionApprovalRequest], Optional[Any]]:
        """
        Executes an approved live workflow run.
        Pauses and returns ActionApprovalRequest when reaching owner_action_approval gate.
        """
        if workflow.status != "ACTIVE":
            raise PermissionError(
                f"Cannot execute workflow '{workflow.id}' because its status is '{workflow.status}'. "
                "Only ACTIVE approved workflows can be executed live."
            )

        context = ExecutionContext(
            tenant_id=workflow.tenant_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            run_id=run_id,
            is_dry_run=False,
            actor_id=reviewer_id,
            extra_meta={
                "action_approved": action_approval_granted,
                "custom_csv_content": csv_content
            }
        )

        topological_nodes = cls._get_topological_order(workflow)
        node_outputs: Dict[str, Any] = {}
        node_traces: List[NodeRunEvent] = []
        pending_action: Optional[ActionApprovalRequest] = None
        final_output: Optional[Any] = None

        for node in topological_nodes:
            tool = ToolRegistry.get(node.tool_type, node.tool_version)
            if not tool:
                raise ValueError(f"Tool '{node.tool_type}' not found.")

            resolved_inputs = cls._resolve_input_bindings(node, node_outputs, context)

            # Check if this node is governance action approval and approval is not yet granted
            if tool.metadata.requires_action_approval and not action_approval_granted:
                # Pause run for human review
                act_type = node.config.get("action_type", "ACTION_APPROVAL")
                first_val = list(resolved_inputs.values())[0] if resolved_inputs else None
                preview = first_val.model_dump() if hasattr(first_val, "model_dump") else (first_val if isinstance(first_val, dict) else {"data": "preview"})

                pending_action = ActionApprovalRequest(
                    action_id=f"act_{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    node_id=node.id,
                    action_type=act_type,
                    preview_data=preview,
                    created_at=datetime.now().isoformat()
                )
                node_traces.append(NodeRunEvent(
                    node_id=node.id,
                    tool_type=node.tool_type,
                    status=RunStatus.WAITING_APPROVAL,
                    started_at=datetime.now().isoformat()
                ))
                return RunStatus.WAITING_APPROVAL, node_traces, pending_action, None

            start_time = datetime.now()
            t0 = time.perf_counter()
            status = RunStatus.COMPLETED
            error_msg = None
            out_snapshot = None

            try:
                cfg_model = tool.validate_config(node.config)
                in_model = tool.validate_inputs(resolved_inputs)
                out_result = await tool.execute(cfg_model, in_model, context)
                node_outputs[node.id] = out_result
                out_snapshot = out_result.model_dump() if hasattr(out_result, "model_dump") else str(out_result)
                final_output = out_result
            except Exception as exc:
                status = RunStatus.FAILED
                error_msg = str(exc)

            t1 = time.perf_counter()
            node_traces.append(NodeRunEvent(
                node_id=node.id,
                tool_type=node.tool_type,
                status=status,
                started_at=start_time.isoformat(),
                completed_at=datetime.now().isoformat(),
                input_snapshot=cls._serialize_snapshot(resolved_inputs),
                output_snapshot=out_snapshot if status == RunStatus.COMPLETED else None,
                error_message=error_msg,
                execution_time_ms=round((t1 - t0) * 1000, 2)
            ))

            if status == RunStatus.FAILED:
                return RunStatus.FAILED, node_traces, None, None

        return RunStatus.COMPLETED, node_traces, None, final_output

    @classmethod
    def _serialize_snapshot(cls, data: Any) -> Optional[Dict[str, Any]]:
        """Ensures input and output data snapshots are valid JSON-serializable dictionaries for memory logging."""
        if data is None:
            return None
        if hasattr(data, "model_dump"):
            return data.model_dump()
        if isinstance(data, dict):
            clean = {}
            for k, v in data.items():
                if hasattr(v, "model_dump"):
                    clean[k] = v.model_dump()
                elif isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                    clean[k] = v
                else:
                    clean[k] = str(v)
            return clean
        if isinstance(data, (list, str, int, float, bool)):
            return {"value": data}
        return {"repr": str(data)}

    @classmethod
    def _get_topological_order(cls, workflow: WorkflowDefinition) -> List[WorkflowNode]:
        """Returns nodes in topological dependency order."""
        adj: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = {node.id: 0 for node in workflow.nodes}
        node_map = {n.id: n for n in workflow.nodes}

        for edge in workflow.edges:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        ordered: List[WorkflowNode] = []

        while queue:
            curr = queue.popleft()
            if curr in node_map:
                ordered.append(node_map[curr])
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return ordered

    @classmethod
    def _resolve_input_bindings(
        cls,
        node: WorkflowNode,
        node_outputs: Dict[str, Any],
        context: ExecutionContext
    ) -> Dict[str, Any]:
        """Resolves input data parameters for a node from previous node outputs."""
        inputs: Dict[str, Any] = {}

        # Special case for upload_vendor_list: inject custom CSV if present
        if node.tool_type == "retail.upload_vendor_list" and "custom_csv_content" in context.extra_meta:
            inputs["csv_content"] = context.extra_meta["custom_csv_content"]

        for param_name, binding_expr in node.input_bindings.items():
            if "." in binding_expr:
                parts = binding_expr.split(".")
                src_node_id = parts[0]
                upstream_out = node_outputs.get(src_node_id)

                if upstream_out is not None:
                    # If binding is "source_node.output", pass upstream_out directly
                    if len(parts) == 2 and parts[1] == "output":
                        inputs[param_name] = upstream_out
                    elif len(parts) > 2:
                        field_name = parts[2]
                        if hasattr(upstream_out, field_name):
                            inputs[param_name] = getattr(upstream_out, field_name)
                        elif isinstance(upstream_out, dict):
                            inputs[param_name] = upstream_out.get(field_name)
                        else:
                            inputs[param_name] = upstream_out
                    else:
                        inputs[param_name] = upstream_out
        return inputs
