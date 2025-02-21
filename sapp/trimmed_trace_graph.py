# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from collections import Counter
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .models import SharedTextKind, TraceFrame, TraceFrameAnnotation, TraceKind
from .trace_graph import TraceGraph


class TrimmedTraceGraph(TraceGraph):
    """Represents a trimmed graph that is constructed from a bigger TraceGraph
    based on issues that have traces involving a set of affected files or
    directories.
    """

    def __init__(
        self, affected_files: List[str], affected_issues_only: bool = False
    ) -> None:
        """Creates an empty TrimmedTraceGraph."""
        super().__init__()
        self._affected_files = affected_files
        self._affected_issues_only = affected_issues_only
        self._visited_trace_frame_ids: Set[int] = set()

    def populate_from_trace_graph(self, graph: TraceGraph) -> None:
        """Populates this graph from the given one based on affected_files"""
        # Track which trace frames have been visited as we populate the full
        # traces of the graph.
        self._visited_trace_frame_ids: Set[int] = set()

        self._populate_affected_issues(graph)

        if not self._affected_issues_only:
            # Finds issues from the conditions and saves them.
            # Also saves traces that have been trimmed to the affected
            # conditions.
            self._populate_issues_from_affected_trace_frames(graph)

            # Traces populated above may be missing all traces because
            # _populate_issues_from_affected_trace_frames only populates
            # traces that reach the affected conditions in one direction. We
            # may need to populate traces in other directions too.
            #
            # For example:
            #
            # Issue_x reaches affected_file_x via postcondition_x (forward
            # trace, i.e. trace leading to source). None of its backward
            # traces (leading to sinks) reach the affected files.
            #
            # _populate_issues_from_affected_trace_frames would have copied its
            # forward traces and trimmed it to those reaching postcondition_x.
            # We cannot blindly populate all forward traces in this case as
            # branches not leading to postcondition_x are unnecessary.
            #
            # However, in this specific example, all backward traces are needed
            # to give a complete picture of which sinks the issue reaches.
            # The following ensures that.
            for instance_id in self._issue_instances.keys():
                first_hop_ids = self._issue_instance_trace_frame_assoc[instance_id]
                fwd_trace_ids = {
                    tf_id
                    for tf_id in first_hop_ids
                    if self._trace_frames[tf_id].kind == TraceKind.POSTCONDITION
                }
                bwd_trace_ids = {
                    tf_id
                    for tf_id in first_hop_ids
                    if self._trace_frames[tf_id].kind == TraceKind.PRECONDITION
                }

                if len(fwd_trace_ids) == 0:
                    self._populate_issue_trace(
                        graph, instance_id, TraceKind.POSTCONDITION
                    )

                if len(bwd_trace_ids) == 0:
                    self._populate_issue_trace(
                        graph, instance_id, TraceKind.PRECONDITION
                    )

        self._recompute_instance_properties()

    # pyre-fixme[3]: Return type must be annotated.
    def _recompute_instance_properties(self):
        """Some properties of issue instances will be affected after trimming
        such as min trace length to leaves. This should be called after the
        trimming to re-compute these values.
        """
        callables_histo = Counter(
            inst.callable_id.local_id for inst in self._issue_instances.values()
        )

        for inst in self._issue_instances.values():
            inst.min_trace_length_to_sources = self._get_min_depth_to_sources(
                inst.id.local_id
            )
            inst.min_trace_length_to_sinks = self._get_min_depth_to_sinks(
                inst.id.local_id
            )
            inst.callable_count = callables_histo[inst.callable_id.local_id]

    def _get_min_depth_to_sources(self, instance_id: int) -> int:
        """Returns shortest depth to source from the issue instance. Instances
        have a pre-computed min_trace_length_to_source, but this can change
        after traces get trimmed from the graph. This re-computes it and
        returns the min.
        """
        first_hop_tf_ids = {
            tf_id
            for tf_id in self._issue_instance_trace_frame_assoc[instance_id]
            if self.get_trace_frame_from_id(tf_id).kind == TraceKind.POSTCONDITION
        }
        return self._get_min_leaf_depth(first_hop_tf_ids)

    def _get_min_depth_to_sinks(self, instance_id: int) -> int:
        """See get_min_depths_to_sources."""
        first_hop_tf_ids = {
            tf_id
            for tf_id in self._issue_instance_trace_frame_assoc[instance_id]
            if self.get_trace_frame_from_id(tf_id).kind == TraceKind.PRECONDITION
        }
        return self._get_min_leaf_depth(first_hop_tf_ids)

    def _get_min_leaf_depth(self, first_hop_tf_ids: Set[int]) -> int:
        min_depth = None
        for tf_id in first_hop_tf_ids:
            leaf_depths = self._trace_frame_leaf_assoc[tf_id]
            for (leaf_id, depth) in leaf_depths:
                kind = self.get_shared_text_by_local_id(leaf_id).kind
                if kind == SharedTextKind.source or kind == SharedTextKind.sink:
                    if depth is not None and (min_depth is None or depth < min_depth):
                        min_depth = depth
        if min_depth is not None:
            return min_depth
        return 0

    def _populate_affected_issues(self, graph: TraceGraph) -> None:
        """Populates the trimmed graph with issues whose locations are in
        affected_files based on data in the input graph. Since these issues
        exist in the affected files, all traces are copied as well.
        """
        affected_instance_ids = [
            instance.id.local_id
            for instance in graph._issue_instances.values()
            if self._is_filename_prefixed_with(
                graph.get_text(instance.filename_id), self._affected_files
            )
        ]

        for instance_id in affected_instance_ids:
            if instance_id in self._issue_instances:
                continue
            self._populate_issue_and_traces(graph, instance_id)

    def _get_sink_kinds(self, graph: TraceGraph, instance_id: int) -> Set[int]:
        kind: SharedTextKind = SharedTextKind.SINK
        sinks = graph.get_issue_instance_shared_texts(instance_id, kind)
        return {sink.id.local_id for sink in sinks}

    def _get_source_kinds(self, graph: TraceGraph, instance_id: int) -> Set[int]:
        kind: SharedTextKind = SharedTextKind.SOURCE
        sources = graph.get_issue_instance_shared_texts(instance_id, kind)
        return {source.id.local_id for source in sources}

    def _get_instance_leaf_ids(self, graph: TraceGraph, instance_id: int) -> Set[int]:
        return self._get_source_kinds(graph, instance_id).union(
            self._get_sink_kinds(graph, instance_id)
        )

    def _populate_issues_from_affected_trace_frames(self, graph: TraceGraph) -> None:
        """TraceFrames found in affected_files should be reachable via some
        issue instance. Follow traces of graph to find them and
        populate this TrimmedGraph with it.
        """

        initial_trace_frames = [
            trace_frame
            for trace_frame in graph._trace_frames.values()
            if self._is_filename_prefixed_with(
                graph.get_text(trace_frame.filename_id), self._affected_files
            )
        ]

        self._populate_issues_from_affected_conditions(
            initial_trace_frames,
            graph,
        )

    def _get_issue_instances_from_frame_id(
        self, graph: TraceGraph, trace_frame_id: int
    ) -> Set[int]:
        return graph._trace_frame_issue_instance_assoc[trace_frame_id]

    def _get_predecessor_frames(
        self, graph: TraceGraph, leaves: Set[int], trace_frame: TraceFrame
    ) -> List[Tuple[TraceFrame, Set[int]]]:
        """Returns predecessor frames paired with leaf kinds to follow for those frames"""
        result = []
        # pyre-fixme[6]: Enums and str are the same but Pyre doesn't think so.
        for trace_frame_id in graph._trace_frames_rev_map[trace_frame.kind][
            (trace_frame.caller_id.local_id, trace_frame.caller_port)
        ]:
            predecessor = graph._trace_frames[trace_frame_id]
            assert predecessor.leaf_mapping is not None
            pred_kinds = graph.compute_prev_leaf_kinds(leaves, predecessor.leaf_mapping)
            result.append((predecessor, pred_kinds))
        return result

    def _populate_issues_from_affected_conditions(
        self,
        # pyre-fixme[2]: Parameter must be annotated.
        initial_conditions,
        graph: TraceGraph,
    ) -> None:
        """Helper for populating reachable issue instances from the initial
        pre/postconditions. Also populates conditions/traces reachable from
        these instances. Traces are populated only in the direction this is
        called from: i.e. if initial_conditions are preconditions, only the
        backward trace is populated.

        Params:
        initial_conditions: The initial collection of pre/postconditions to
        start searching for issues from.

        graph: The trace graph to search for issues. Nodes/edges in this graph
        will be copied over to the local state
        """
        visited: Dict[int, Set[int]] = {}
        que = [
            (frame, graph.get_incoming_leaf_kinds_of_frame(frame))
            for frame in initial_conditions
        ]

        # Note that parent conditions may not transitively lead to the leaves
        # that its descendents lead to due to special-cased leaf filtering at
        # analysis time. When visiting each condition, we need to track the
        # leaves that we are visiting it from and only visit parent traces that
        # share common leaves along the path.
        while len(que) > 0:
            condition, leaves = que.pop()
            cond_id = condition.id.local_id

            if cond_id in visited:
                leaves = leaves - visited[cond_id]
                if len(leaves) == 0:
                    continue
                else:
                    visited[cond_id].update(leaves)
            else:
                visited[cond_id] = leaves

            # Found instance(s) related to the current condition. Yay.
            # This instance may have been found before, but process it again
            # anyway because we need to add the assoc with this condition.
            for instance_id in self._get_issue_instances_from_frame_id(graph, cond_id):
                # Check if the leaves (sources/sinks) of the issue reach
                # the same leaves as the ones relevant to this condition.
                instance = graph._issue_instances[instance_id]
                issue_leaves = set(
                    self._get_instance_leaf_ids(graph, instance.id.local_id)
                )
                common_leaves = issue_leaves.intersection(leaves)
                if len(common_leaves) > 0:
                    if instance_id not in self._issue_instances:
                        self._populate_issue(graph, instance_id)
                    self.add_issue_instance_trace_frame_assoc(instance, condition)

            # Conditions that call this may have originated from other issues,
            # keep searching for parent conditions leading to this one.
            for (next_frame, frame_leaves) in self._get_predecessor_frames(
                graph, leaves, condition
            ):
                if len(frame_leaves) > 0:
                    que.append((next_frame, frame_leaves))

        # Add traces leading out from initial_conditions, and all visited
        # conditions leading back towards the issues.
        initial_condition_ids = [
            condition.id.local_id for condition in initial_conditions
        ]
        self._populate_trace(graph, initial_condition_ids)
        for frame_id in visited:
            self._add_trace_frame(graph, graph._trace_frames[frame_id])

    def _populate_issue_and_traces(self, graph: TraceGraph, instance_id: int) -> None:
        """Copies an issue over from the given trace graph, including all its
        traces and assocs.
        """
        self._populate_issue(graph, instance_id)
        self._populate_issue_trace(graph, instance_id)

    def _populate_issue_trace(
        self, graph: TraceGraph, instance_id: int, kind: Optional[TraceKind] = None
    ) -> None:
        trace_frame_ids = list(graph._issue_instance_trace_frame_assoc[instance_id])
        instance = graph._issue_instances[instance_id]
        filtered_ids = []
        for trace_frame_id in trace_frame_ids:
            frame = graph._trace_frames[trace_frame_id]
            if kind is None or kind == frame.kind:
                self.add_issue_instance_trace_frame_assoc(instance, frame)
                filtered_ids.append(trace_frame_id)
        self._populate_trace(graph, filtered_ids)

    def _populate_issue(self, graph: TraceGraph, instance_id: int) -> None:
        """Adds an issue to the trace graph along with relevant information
        pertaining to the issue (e.g. instance, fix_info, sources/sinks)
        The issue is identified by its corresponding instance's ID in the input
        trace graph.
        """
        instance = graph._issue_instances[instance_id]
        issue = graph._issues[instance.issue_id.local_id]
        self._populate_shared_text(graph, instance.message_id)
        self._populate_shared_text(graph, instance.filename_id)
        self._populate_shared_text(graph, instance.callable_id)

        self.add_issue_instance(instance)
        self.add_issue(issue)

        if instance_id in graph._issue_instance_fix_info:
            issue_fix_info = graph._issue_instance_fix_info[instance_id]
            self.add_issue_instance_fix_info(instance, issue_fix_info)

        for shared_text_id in graph._issue_instance_shared_text_assoc[instance_id]:
            shared_text = graph._shared_texts[shared_text_id]
            if shared_text_id not in self._shared_texts:
                self.add_shared_text(shared_text)
            self.add_issue_instance_shared_text_assoc(instance, shared_text)

    def _populate_trace(self, graph: TraceGraph, trace_frame_ids: List[int]) -> None:
        """Populates (from the given trace graph) the forward and backward
        traces reachable from the given traces (including input trace frames).
        Make sure to respect trace kind in successors
        """
        while len(trace_frame_ids) > 0:
            trace_frame_id = trace_frame_ids.pop()
            if trace_frame_id in self._visited_trace_frame_ids:
                continue

            trace_frame = graph._trace_frames[trace_frame_id]
            self._add_trace_frame(graph, trace_frame)
            self._visited_trace_frame_ids.add(trace_frame_id)

            trace_frame_ids.extend(
                [
                    next_frame.id.local_id
                    for next_frame in graph.get_next_trace_frames(trace_frame)
                    if next_frame.id.local_id not in self._visited_trace_frame_ids
                ]
            )

    def _add_trace_frame(self, graph: TraceGraph, trace_frame: TraceFrame) -> None:
        """Copies the trace frame from 'graph' to this (self) graph.
        Also copies all the trace_frame-leaf assocs since we don't
        know which ones are needed until we know the issue that reaches it
        """
        trace_frame_id = trace_frame.id.local_id
        self.add_trace_frame(trace_frame)

        annotations = graph.get_condition_annotations(trace_frame_id)
        for annotation in annotations:
            self._add_trace_annotation(graph, annotation)

        self._populate_shared_text(graph, trace_frame.filename_id)
        self._populate_shared_text(graph, trace_frame.caller_id)
        self._populate_shared_text(graph, trace_frame.callee_id)
        for (leaf_id, depth) in graph._trace_frame_leaf_assoc[trace_frame_id]:
            leaf = graph._shared_texts[leaf_id]
            if leaf_id not in self._shared_texts:
                self.add_shared_text(leaf)
            self.add_trace_frame_leaf_assoc(trace_frame, leaf, depth)

    @staticmethod
    def _is_filename_prefixed_with(filename: str, prefixes: Iterable[str]) -> bool:
        return any(filename.startswith(p) for p in prefixes)

    # pyre-fixme[2]: Parameter must be annotated.
    # pyre-fixme[2]: Parameter must be annotated.
    def _populate_shared_text(self, graph, id) -> None:
        text = graph._shared_texts[id.local_id]
        if text.id.local_id not in self._shared_texts:
            self.add_shared_text(text)

    def _add_trace_annotation(
        self, graph: TraceGraph, annotation: TraceFrameAnnotation
    ) -> None:
        """Copies the annotation from 'graph' to this (self) graph.
        Also copies children TraceFrames of the annotation (if any). The
        parent TraceFrame of the annotation is NOT copied.
        """
        self.add_trace_annotation(annotation)
        children = graph.get_annotation_trace_frames(annotation.id.local_id)
        child_ids = [child.id.local_id for child in children]
        for child in children:
            self.add_trace_frame_annotation_trace_frame_assoc(annotation, child)
        self._populate_trace(graph, child_ids)
