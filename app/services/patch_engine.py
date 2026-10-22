"""
app/services/patch_engine.py
Typed Patch Engine for Kirata AI Workflow Builder.
Applies typed atomic patch operations to a base WorkflowDefinition,
calculates visual diffs, and creates new candidate workflow drafts.
"""

import copy
from typing import Dict, List, Tuple, Any
from app.schemas.workflow import (
    WorkflowDefinition,
    WorkflowNode,
    WorkflowEdge,
    PatchOperation,
    PatchOpType,
    GraphDiff,
    NodeVisualDiff,
    NodeDiffStatus,
    WorkflowStatus
)


class PatchEngine:
    """
    Applies typed patch operations to a base canonical workflow and computes visual diffs.
    """

    @classmethod
    def apply_patch(
        cls,
        base_workflow: WorkflowDefinition,
        operations: List[PatchOperation],
        new_version: int,
        rationale: str = ""
    ) -> Tuple[WorkflowDefinition, GraphDiff]:
        # Deepcopy to keep base workflow completely immutable
        patched_wf = copy.deepcopy(base_workflow)
        patched_wf.version = new_version
        patched_wf.status = WorkflowStatus.DRAFT

        # Maps for quick lookup
        node_map: Dict[str, WorkflowNode] = {n.id: n for n in patched_wf.nodes}
        base_node_map: Dict[str, WorkflowNode] = {n.id: copy.deepcopy(n) for n in base_workflow.nodes}

        added_node_ids: List[str] = []
        removed_node_ids: List[str] = []
        modified_node_configs: Dict[str, Dict[str, Dict[str, Any]]] = {}

        added_edges: List[str] = []
        removed_edges: List[str] = []

        for op in operations:
            if op.op == PatchOpType.UPDATE_NODE_CONFIG:
                if op.node_id and op.node_id in node_map and op.config_patch:
                    node = node_map[op.node_id]
                    before_config = copy.deepcopy(node.config)
                    node.config.update(op.config_patch)

                    if op.node_id not in modified_node_configs:
                        modified_node_configs[op.node_id] = {}

                    for key, new_val in op.config_patch.items():
                        old_val = before_config.get(key)
                        modified_node_configs[op.node_id][key] = {
                            "before": old_val,
                            "after": new_val
                        }

            elif op.op == PatchOpType.INSERT_NODE:
                if op.node_data and op.node_data.id not in node_map:
                    node_map[op.node_data.id] = op.node_data
                    patched_wf.nodes.append(op.node_data)
                    added_node_ids.append(op.node_data.id)

            elif op.op == PatchOpType.REMOVE_NODE:
                if op.node_id and op.node_id in node_map:
                    patched_wf.nodes = [n for n in patched_wf.nodes if n.id != op.node_id]
                    # Also remove any edges referencing this node
                    orphaned_edges = [
                        e.id or f"e_{e.source}_{e.target}"
                        for e in patched_wf.edges
                        if e.source == op.node_id or e.target == op.node_id
                    ]
                    removed_edges.extend(orphaned_edges)
                    patched_wf.edges = [
                        e for e in patched_wf.edges
                        if e.source != op.node_id and e.target != op.node_id
                    ]
                    del node_map[op.node_id]
                    removed_node_ids.append(op.node_id)

            elif op.op == PatchOpType.ADD_EDGE:
                if op.edge:
                    edge_id = op.edge.id or f"e_{op.edge.source}_{op.edge.target}"
                    if not any(e.source == op.edge.source and e.target == op.edge.target for e in patched_wf.edges):
                        patched_wf.edges.append(op.edge)
                        added_edges.append(edge_id)

            elif op.op == PatchOpType.REMOVE_EDGE:
                target_edge_id = op.edge_id or (f"e_{op.edge.source}_{op.edge.target}" if op.edge else None)
                if target_edge_id:
                    patched_wf.edges = [
                        e for e in patched_wf.edges
                        if (e.id != target_edge_id and f"e_{e.source}_{e.target}" != target_edge_id)
                    ]
                    removed_edges.append(target_edge_id)

            elif op.op == PatchOpType.UPDATE_APPROVAL_POLICY:
                if op.policy_patch:
                    current_policy = patched_wf.approval_policy.model_dump()
                    current_policy.update(op.policy_patch)
                    patched_wf.approval_policy = patched_wf.approval_policy.model_validate(current_policy)

        # Build Visual Diff
        diff_nodes: List[NodeVisualDiff] = []
        # Check all nodes in new workflow
        for node in patched_wf.nodes:
            if node.id in added_node_ids:
                diff_nodes.append(NodeVisualDiff(
                    node_id=node.id,
                    status=NodeDiffStatus.ADDED,
                    tool_type=node.tool_type,
                    explanation=f"Node baru '{node.id}' ditambahkan ke dalam alur kerja."
                ))
            elif node.id in modified_node_configs:
                diff_nodes.append(NodeVisualDiff(
                    node_id=node.id,
                    status=NodeDiffStatus.MODIFIED,
                    tool_type=node.tool_type,
                    config_changes=modified_node_configs[node.id],
                    explanation=f"Konfigurasi node '{node.id}' diperbarui."
                ))
            else:
                diff_nodes.append(NodeVisualDiff(
                    node_id=node.id,
                    status=NodeDiffStatus.UNCHANGED,
                    tool_type=node.tool_type
                ))

        # Check removed nodes
        for r_id in removed_node_ids:
            old_node = base_node_map.get(r_id)
            diff_nodes.append(NodeVisualDiff(
                node_id=r_id,
                status=NodeDiffStatus.REMOVED,
                tool_type=old_node.tool_type if old_node else "unknown",
                explanation=f"Node '{r_id}' dihapus dari alur kerja."
            ))

        # Generate Human-Friendly Summary in Indonesian
        summary_parts = []
        if added_node_ids:
            summary_parts.append(f"Penambahan {len(added_node_ids)} node ({', '.join(added_node_ids)})")
        if modified_node_configs:
            summary_parts.append(f"Modifikasi konfigurasi pada {len(modified_node_configs)} node ({', '.join(modified_node_configs.keys())})")
        if removed_node_ids:
            summary_parts.append(f"Penghapusan {len(removed_node_ids)} node ({', '.join(removed_node_ids)})")
        if added_edges or removed_edges:
            summary_parts.append(f"Penyesuaian edge (+{len(added_edges)}, -{len(removed_edges)})")

        diff_summary = "; ".join(summary_parts) if summary_parts else "Tidak ada perubahan struktural pada alur kerja."

        diff = GraphDiff(
            nodes=diff_nodes,
            added_edges=added_edges,
            removed_edges=removed_edges,
            summary=diff_summary
        )

        return patched_wf, diff
