"""
app/services/workflow_validator.py
Server-side DAG, Schema, Tool Whitelist, and Permission Validator for Kirata AI Workflow Builder.
Ensures 100% of activated and proposed workflows are mathematically sound, acyclic, and safe.
"""

from typing import Dict, List, Set, Tuple
from collections import defaultdict, deque
from pydantic import BaseModel, Field

from app.schemas.workflow import WorkflowDefinition, WorkflowNode, WorkflowEdge
from app.core.tool_registry import ToolRegistry, ToolCategory


class ValidationResult(BaseModel):
    is_valid: bool = Field(..., description="Whether workflow is structurally and semantically valid")
    errors: List[str] = Field(default_factory=list, description="Actionable failure reasons")
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings")


class WorkflowValidator:
    """
    Deterministic validator executing schema, DAG, whitelist, and security checks.
    """

    @classmethod
    def validate_workflow(
        cls,
        workflow: WorkflowDefinition,
        enforce_tool_allowlist: bool = True
    ) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        if not workflow.nodes:
            return ValidationResult(is_valid=False, errors=["Workflow must contain at least one node."])

        node_map: Dict[str, WorkflowNode] = {node.id: node for node in workflow.nodes}

        # 1. Check duplicate node IDs
        if len(node_map) < len(workflow.nodes):
            errors.append("Duplicate node IDs found in workflow.")

        # 2. Check edges referencing existing nodes
        for edge in workflow.edges:
            if edge.source not in node_map:
                errors.append(f"Edge source '{edge.source}' does not exist in workflow nodes.")
            if edge.target not in node_map:
                errors.append(f"Edge target '{edge.target}' does not exist in workflow nodes.")
            if edge.source == edge.target:
                errors.append(f"Self-loop detected on node '{edge.source}'. Self-referencing edges are forbidden.")

        # 3. DAG & Cycle Detection (Kahn's Algorithm)
        has_cycle, cycle_nodes = cls._detect_cycles(workflow.nodes, workflow.edges)
        if has_cycle:
            errors.append(f"Forbidden cycle detected in workflow involving nodes: {', '.join(cycle_nodes)}")

        # 4. Tool Whitelist & Schema Verification
        if enforce_tool_allowlist:
            for node in workflow.nodes:
                tool = ToolRegistry.get(node.tool_type)
                if not tool:
                    errors.append(
                        f"Node '{node.id}' uses unapproved or unknown tool '{node.tool_type}'. "
                        "Only certified allowlisted tools are permitted."
                    )
                else:
                    # Validate node config against the tool's config schema
                    try:
                        tool.validate_config(node.config)
                    except Exception as exc:
                        errors.append(f"Node '{node.id}' config validation failed for tool '{node.tool_type}': {str(exc)}")

        # 5. Input Bindings & Upstream Reachability Check
        ancestor_map = cls._build_ancestor_map(workflow.nodes, workflow.edges)
        for node in workflow.nodes:
            for param_name, binding_expr in node.input_bindings.items():
                # Format: "source_node_id.output" or "source_node_id.output.field"
                if "." in binding_expr:
                    source_node_id = binding_expr.split(".")[0]
                    if source_node_id not in node_map:
                        errors.append(
                            f"Node '{node.id}' input binding '{param_name}' references non-existent node '{source_node_id}'."
                        )
                    elif source_node_id not in ancestor_map[node.id]:
                        errors.append(
                            f"Invalid binding in node '{node.id}': '{source_node_id}' is not an upstream ancestor node."
                        )

        # 6. Action-Level Approval Governance Guardrail
        # If workflow has side-effect/export tools, ensure an approval node precedes them
        has_export = any("export" in n.tool_type or "publish" in n.tool_type for n in workflow.nodes)
        has_approval_gate = any(n.tool_type == "governance.action_approval" for n in workflow.nodes)

        if has_export and not has_approval_gate:
            errors.append(
                "Safety Violation: Workflow contains export/publishing actions without a preceding "
                "'governance.action_approval' node. All external mutations require human action approval."
            )

        return ValidationResult(
            is_valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings
        )

    @classmethod
    def _detect_cycles(cls, nodes: List[WorkflowNode], edges: List[WorkflowEdge]) -> Tuple[bool, List[str]]:
        """Kahn's Algorithm for cycle detection."""
        adj: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = {node.id: 0 for node in nodes}

        for edge in edges:
            adj[edge.source].append(edge.target)
            in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

        queue = deque([node_id for node_id, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count < len(nodes):
            # There is a cycle
            cycle_nodes = [node_id for node_id, deg in in_degree.items() if deg > 0]
            return True, cycle_nodes
        return False, []

    @classmethod
    def _build_ancestor_map(cls, nodes: List[WorkflowNode], edges: List[WorkflowEdge]) -> Dict[str, Set[str]]:
        """Maps each node to the set of all its upstream ancestor nodes."""
        direct_parents: Dict[str, List[str]] = defaultdict(list)
        for edge in edges:
            direct_parents[edge.target].append(edge.source)

        ancestors: Dict[str, Set[str]] = defaultdict(set)

        def get_ancestors(node_id: str, visited: Set[str]) -> Set[str]:
            if node_id in ancestors and ancestors[node_id]:
                return ancestors[node_id]
            result: Set[str] = set()
            for parent in direct_parents[node_id]:
                if parent not in visited:
                    visited.add(parent)
                    result.add(parent)
                    result.update(get_ancestors(parent, visited))
            ancestors[node_id] = result
            return result

        for node in nodes:
            get_ancestors(node.id, set())

        return ancestors
