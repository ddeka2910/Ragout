#(c) 2013-2014 by Authors
#This file is a part of Ragout program.
#Released under the BSD license (see LICENSE file)

"""
This module tries to detect missassembled adjacencies
in input sequences and breaks them if neccesary
"""

from __future__ import print_function
import logging
from collections import defaultdict
from copy import copy, deepcopy

from ragout.breakpoint_graph.breakpoint_graph import BreakpointGraph

logger = logging.getLogger()

class ChimeraDetector(object):
    def __init__(self, breakpoint_graphs, target_seqs):
        logger.debug("Detecting chimeric adjacencies")
        self.bp_graphs = breakpoint_graphs
        self.target_seqs = target_seqs
        self._make_hierarchical_cuts()

    def _make_hierarchical_cuts(self):
        """
        Determines where and at what synteny blocks scale to break each contig
        """
        ordered_blocks = sorted(self.bp_graphs.keys(), reverse=True)
        seq_cuts = defaultdict(lambda : defaultdict(list))

        #extracting and grouping by sequence
        for block in ordered_blocks:
            chimeric_adj, cuts = self._get_chimeric_adj(self.bp_graphs[block])
            for seq_name in cuts:
                seq_cuts[seq_name][block] = cuts[seq_name]

        #magic!
        hierarchical_cuts = defaultdict(lambda : defaultdict(list))
        for seq_name in seq_cuts:
            logger.debug(seq_name)
            logger.debug(dict(seq_cuts[seq_name]))
            for i in xrange(len(ordered_blocks)):
                top_block = ordered_blocks[i]
                logger.debug(top_block)

                for cur_cut in seq_cuts[seq_name][top_block]:
                    for j in xrange(i + 1, len(ordered_blocks)):
                        lower_block = ordered_blocks[j]
                        #check if there is overlapping cut
                        chosen_cut = None
                        for lower_cut in seq_cuts[seq_name][lower_block]:
                            ovlp_left = max(cur_cut[0], lower_cut[0])
                            ovlp_right = min(cur_cut[1], lower_cut[1])
                            #if so, update current and go down
                            if ovlp_right >= ovlp_left:
                                cur_cut = (ovlp_left, ovlp_right)
                                chosen_cut = cur_cut
                                break
                        if chosen_cut:
                            seq_cuts[seq_name][lower_block].remove(chosen_cut)

                    logger.debug(cur_cut)
                    for k in xrange(i, len(ordered_blocks)):
                        affected_block = ordered_blocks[k]
                        cut_region = (cur_cut[1] + cur_cut[0]) / 2
                        hierarchical_cuts[seq_name][affected_block] \
                                                            .append(cut_region)
            logger.debug(hierarchical_cuts[seq_name])
        self.hierarchical_cuts = hierarchical_cuts

    def _get_chimeric_adj(self, bp_graph):
        """
        Detects chimeric adjacencies
        """
        chimeric_adj = set()
        seq_cuts = defaultdict(list)

        subgraphs = bp_graph.connected_components()
        for subgr in subgraphs:
            if len(subgr.bp_graph) > 100:
                logger.debug("Processing component of size {0}"
                             .format(len(subgr.bp_graph)))

            for (u, v, data) in subgr.bp_graph.edges_iter(data=True):
                genomes = subgr.supporting_genomes(u, v)
                if set(genomes) != set([bp_graph.target]):
                    continue
                if subgr.alternating_cycle(u, v):
                    continue

                gap_seq = (self.target_seqs[data["chr_name"]]
                                           [data["start"]:data["end"]])
                ns_rate = (float(gap_seq.upper().count("N")) / len(gap_seq)
                           if len(gap_seq) else 0)
                if ns_rate < 0.1 and len(subgr.bp_graph) == 4:
                    if self._valid_2break(subgr.bp_graph, (u, v)):
                        logger.debug(ns_rate)
                        for node in subgr.bp_graph.nodes():
                            bp_graph.add_debug_node(node)
                        continue

                chimeric_adj.add((u, v))
                seq_cuts[data["chr_name"]].append((data["start"], data["end"]))

        bp_graph.debug_output()

        return chimeric_adj, seq_cuts

    def _valid_2break(self, bp_graph, red_edge):
        """
        Checks if there is a valid 2-break through the given red edge
        """
        assert len(bp_graph) == 4
        red_1, red_2 = red_edge
        cand_1, cand_2 = tuple(set(bp_graph.nodes()) - set(red_edge))
        if abs(cand_1) == abs(cand_2):
            return False

        if bp_graph.has_edge(red_1, cand_1):
            known_1 = red_1, cand_1
            known_2 = red_2, cand_2
        else:
            known_1 = red_1, cand_2
            known_2 = red_2, cand_1

        chr_1 = {}
        for data in bp_graph[known_1[0]][known_1[1]].values():
            chr_1[data["genome_id"]] = data["chr_name"]
        chr_2 = {}
        for data in bp_graph[known_2[0]][known_2[1]].values():
            chr_2[data["genome_id"]] = data["chr_name"]
        common_genomes = set(chr_1.keys()).intersection(chr_2.keys())
        for genome in common_genomes:
            if chr_1[genome] != chr_2[genome]:
                return False

        return True

    def break_contigs(self, perm_container, block_size):
        """
        Breaks contigs in inferred cut positions
        """
        perm_container.target_perms = self._cut_permutations(block_size,
                                                perm_container.target_perms)
        perm_container.filter_indels(True)

    #TODO: refactoring
    def _cut_permutations(self, block_size, permutations):
        """
        Actually breaks these contigs
        """
        new_perms = []
        num_chim_perms = 0
        num_cuts = 0
        num_lost = 0
        for perm in permutations:
            cuts = self.hierarchical_cuts[perm.chr_name][block_size]
            if not cuts:
                new_perms.append(perm)
                continue

            #logger.debug("Original {0}".format(perm))
            cuts_stack = copy(sorted(cuts))
            cuts_stack.append(perm.seq_len)
            cur_perm = deepcopy(perm)
            cur_perm.blocks = []
            shift = 0

            num_chim_perms += 1
            num_cuts += len(cuts_stack) - 1

            for block in perm.blocks:
                if block.end <= cuts_stack[0]:
                    block.start -= shift
                    block.end -= shift
                    cur_perm.blocks.append(block)
                    continue

                if block.start < cuts_stack[0]:
                    num_lost += 1
                    continue

                #we have passed the current cut
                cur_perm.seq_start = shift
                cur_perm.seq_end = cuts_stack[0]
                new_perms.append(cur_perm)
                #logger.debug(cur_perm)

                shift = cuts_stack[0]
                cuts_stack.pop(0)

                cur_perm = deepcopy(perm)
                block.start -= shift
                block.end -= shift
                cur_perm.blocks = [block]

            cur_perm.seq_start = shift
            cur_perm.seq_end = cuts_stack[0]
            new_perms.append(cur_perm)
            #logger.debug(cur_perm)

        logger.debug("Chimera Detector: {0} cuts made in {1} sequences"
                        .format(num_cuts, num_chim_perms))
        logger.debug("Lost {0} blocks".format(num_lost))
        return new_perms
