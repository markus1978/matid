from __future__ import absolute_import, division, print_function, unicode_literals

import itertools

from collections import deque, defaultdict

import numpy as np

import networkx as nx

from ase import Atoms
from ase.visualize import view

import systax.geometry
from systax.core.linkedunits import LinkedUnitCollection, LinkedUnit


class PeriodicFinder():
    """Used to find translationally periodic structures within atomic systems.
    """
    def __init__(self, pos_tol, seed_algorithm, max_cell_size):
        """
        """
        self.pos_tol = pos_tol
        self.seed_algorithm = seed_algorithm
        self.max_cell_size = max_cell_size

    def get_regions(self, system, vacuum_dir, tesselation_distance):
        """Tries to find the periodic regions, like surfaces, in an atomic
        system.

        Args:
            system(ase.Atoms): The system from which to find the periodic
                regions.
            vacuum_dir(sequence three booleans): The cell basis directions that
                have a vacuum gap.

        Returns:
            list of tuples: A list of tuples containing the following information:
                indices: Indices of the atoms belonging to a region
                linkedunitcollection: A LinkedUnitCollection object
                    representing the region
                atoms: An ASE.Atoms object representing the region
                unit cell: An ASE.Atoms object representing the unit cell of the region.
        """
        # Find the seed points
        if self.seed_algorithm == "cm":
            seed_points = [self._find_seed_cm(system)]

        # Find possible bases for each seed point
        regions = []

        for seed_index in seed_points:
            possible_spans, neighbour_mask = self._find_possible_bases(system, seed_index)
            proto_cell, offset, dim = self._find_proto_cell(
                system,
                seed_index,
                possible_spans,
                neighbour_mask,
                vacuum_dir
            )

            # 1D is not handled
            if dim == 1 or proto_cell is None:
                return []

            # The indices of the periodic dimensions.
            periodic_indices = list(range(dim))

            view(proto_cell)
            # print(seed_position)

            # Find a region that is spanned by the found unit cell
            unit_collection = self._find_periodic_region(
                system,
                vacuum_dir,
                dim == 2,
                tesselation_distance,
                seed_index,
                proto_cell,
                offset,
                periodic_indices)

            i_indices = unit_collection.get_basis_indices()
            # rec = unit_collection.recreate_valid()
            # view(rec)

            if len(i_indices) > 0:
                regions.append((i_indices, unit_collection, proto_cell))

        # Combine regions that are identical. The algorithm used here is
        # suboptimal but there are not so many regions here to compare.
        n_regions = len(regions)
        similarity = np.zeros((n_regions, n_regions))
        intersection_threshold = 0.9
        for i in range(n_regions):
            for j in range(n_regions):
                if j > i:
                    i_ind = set(regions[i][0])
                    j_ind = set(regions[j][0])
                    l_total = len(i_ind.union(j_ind))
                    l_intersection = i_ind.intersection(j_ind)
                    l_sim = l_intersection/l_total
                    similarity[i, j] = l_sim

        # Return only dissimilar regions
        dissimilar = []
        initial = set(range(n_regions))
        for i in range(n_regions):
            for j in initial:
                if j >= i:
                    l_sim = similarity[i, j]
                    if l_sim >= intersection_threshold:
                        initial.remove(i)
                    dissimilar.append(j)

        # See if the found cell is OK
        region_tuples = []
        for i_region in dissimilar:
            l_ind, l_coll, l_cell = regions[i_region]
            region_tuples.append(l_coll)

        return region_tuples

    def _find_possible_bases(self, system, seed_index):
        """Finds all the possible vectors that might span a cell.
        """
        # Calculate a displacement tensor that takes into account the
        # periodicity of the system
        pos = system.get_positions()
        pbc = system.get_pbc()
        cell = system.get_cell()
        disp_tensor = systax.geometry.get_displacement_tensor(pos, pos, cell, pbc, mic=True)

        # If the search radius exceeds beyond the periodic boundaries, extend the system
        # Get the vectors that span from the seed to all other atoms
        # disp_tensor = syscache["disp_tensor"]
        seed_spans = disp_tensor[:, seed_index]
        atomic_numbers = system.get_atomic_numbers()

        # Find indices of atoms that are identical to seed atom
        seed_element = atomic_numbers[seed_index]
        identical_elem_mask = (atomic_numbers == seed_element)

        # Only keep spans that are smaller than the maximum vector length
        seed_span_lengths = np.linalg.norm(seed_spans, axis=1)
        distance_mask = (seed_span_lengths < self.max_cell_size)
        # syscache["neighbour_mask"] = distance_mask

        # Form a combined mask and filter spans with it
        combined_mask = (distance_mask) & (identical_elem_mask)
        combined_mask[seed_index] = False  # Ignore self
        bases = seed_spans[combined_mask]

        return bases, distance_mask

    def _find_proto_cell(self, system, seed_index, possible_spans, neighbour_mask, vacuum_dir):
        """Used to find the best candidate for a unit cell basis that could
        generate a periodic region in the structure.

        Args:

        Returns:
            ase.Atoms: A system representing the best cell that was found
            np.ndarray: Position of the seed atom in the cell
        """
        vacuum_dir = np.array(vacuum_dir)
        positions = system.get_positions()
        numbers = system.get_atomic_numbers()

        # Find how many and which of the neighbouring atoms have a periodic
        # copy in the found directions
        neighbour_pos = positions[neighbour_mask]
        neighbour_num = numbers[neighbour_mask]
        neighbour_indices = np.where(neighbour_mask)[0]
        n_neighbours = len(neighbour_pos)
        n_spans = len(possible_spans)
        metric = np.empty((len(possible_spans)), dtype=int)
        metric_per_atom_per_span = np.zeros((n_neighbours, n_spans))
        adjacency_lists = []
        for i_span, span in enumerate(possible_spans):
            i_adj_list = defaultdict(list)
            add_pos = neighbour_pos + span
            sub_pos = neighbour_pos - span
            add_indices, _, _, _ = systax.geometry.get_matches(system, add_pos, neighbour_num, self.pos_tol)
            sub_indices, _, _, _ = systax.geometry.get_matches(system, sub_pos, neighbour_num, self.pos_tol)

            n_metric = 0
            for i_neigh in range(n_neighbours):
                i_add = add_indices[i_neigh]
                i_sub = sub_indices[i_neigh]
                if i_add is not None:
                    n_metric += 1
                    metric_per_atom_per_span[i_neigh, i_span] += 1
                    i_adj_list[neighbour_indices[i_neigh]].append(i_add)
                if i_sub is not None:
                    n_metric += 1
                    metric_per_atom_per_span[i_neigh, i_span] += 1
                    i_adj_list[neighbour_indices[i_neigh]].append(i_sub)
            metric[i_span] = n_metric
            adjacency_lists.append(i_adj_list)

        # Get the spans that come from the periodicity if they are smaller than
        # the maximum cell size
        periodic_spans = system.get_cell()[~vacuum_dir]
        periodic_span_lengths = np.linalg.norm(periodic_spans, axis=1)
        periodic_spans = periodic_spans[periodic_span_lengths < self.max_cell_size]
        n_periodic_spans = len(periodic_spans)
        if n_periodic_spans != 0:
            periodic_metric = 2*n_neighbours*np.ones((n_periodic_spans))
            possible_spans = np.concatenate((possible_spans, periodic_spans), axis=0)
            metric = np.concatenate((metric, periodic_metric), axis=0)
            for per_span in periodic_spans:
                per_adjacency_list = defaultdict(list)
                for i_neigh in neighbour_indices:
                    per_adjacency_list[i_neigh].extend([i_neigh, i_neigh])
                adjacency_lists.append(per_adjacency_list)

        # Find the directions that are most repeat the neighbours above some
        # preset threshold. This is used to eliminate directions that are
        # caused by pure chance. The maximum score that a direction can get is
        # 2*n_neighbours. We specify that the score must be above 25% percent
        # of this maximum score to be considered a valid direction.
        span_lengths = np.linalg.norm(possible_spans, axis=1)
        normed_spans = possible_spans/span_lengths[:, np.newaxis]
        dots = np.inner(possible_spans, possible_spans)
        max_span_indices = np.where(metric > 0.5*n_neighbours)
        v2 = normed_spans[max_span_indices]
        if len(v2) == 0:
            return None, None, None

        # Find the dimensionality of the space spanned by this set of vectors
        n_dim = self._find_space_dim(v2)

        # Currently 1D is not handled
        if n_dim == 1:
            return None, None, 1

        # Find best valid combination of spans by looking at the number of
        # neighbours that are periodically repeated by the spans, the
        # orthogonality of the spans and the length of the spans
        span_indices = list(range(len(possible_spans)))
        combos = np.array(list(itertools.combinations(span_indices, n_dim)))

        # First sort by the number of periodic neighbours that are generated.
        # This way we first choose spans that create most periodic neighbours.
        periodicity_scores = np.zeros(len(combos))
        for i_dim in range(n_dim):
            i_combos = combos[:, i_dim]
            i_scores = metric[i_combos]
            periodicity_scores += i_scores
        periodicity_indices = np.argsort(-periodicity_scores)

        # Iterate over the combos until a linearly independent combo is found
        # and stalemates have been resolved by checking the orthogonality and
        # vector lengths.
        best_periodicity_score = 0
        best_score = float('inf')
        best_combo = None
        for index in periodicity_indices:

            combo = combos[index]
            i_per_score = periodicity_scores[index]

            # Check that the combination is linearly independent
            area_threshold = 0.1
            volume_threshold = 0.1
            if n_dim == 1:
                i = combo[0]
                i_score = span_lengths[i]

            if n_dim == 2:
                i = combo[0]
                j = combo[1]
                a_norm = normed_spans[i]
                b_norm = normed_spans[j]
                orthogonality = np.linalg.norm(np.cross(a_norm, b_norm))
                if orthogonality < area_threshold:
                    continue
                else:
                    ortho_score = abs(dots[i, j])
                    norm_score = span_lengths[i] + span_lengths[j]
                    i_score = ortho_score + norm_score

            elif n_dim == 3:
                i = combo[0]
                j = combo[1]
                k = combo[2]
                a_norm = normed_spans[i]
                b_norm = normed_spans[j]
                c_norm = normed_spans[k]
                orthogonality = np.dot(np.cross(a_norm, b_norm), c_norm)
                if orthogonality < volume_threshold:
                    continue
                else:
                    ortho_score = abs(dots[i, j]) + abs(dots[j, k]) + abs(dots[i, k])
                    norm_score = span_lengths[i] + span_lengths[j] + span_lengths[k]
                    i_score = ortho_score + norm_score

            if i_per_score >= best_periodicity_score:
                best_periodicity_score = i_per_score
                if i_score < best_score:
                    best_score = i_score
                    best_combo = combo
            else:
                if best_combo is not None:
                    break

        best_spans = possible_spans[best_combo]
        n_spans = len(best_spans)

        # Create a full periodicity graph for the found basis
        periodicity_graph = None
        full_adjacency_list = defaultdict(list)
        for i_span in best_combo:
            adjacency_list = adjacency_lists[i_span]
            for key, value in adjacency_list.items():
                full_adjacency_list[key].extend(value)
        periodicity_graph = nx.MultiGraph(full_adjacency_list)

        # import matplotlib.pyplot as plt
        # plt.subplot(111)
        # nx.draw(periodicity_graph)
        # plt.show()

        # Get all disconnected subgraphs
        graphs = list(nx.connected_component_subgraphs(periodicity_graph))

        # Eliminate subgraphs that do not have enough periodicity
        valid_graphs = []
        for graph in graphs:

            # The periodicity is measured by the average degree of the nodes.
            # The graph allows multiple edges, and edges that have the same
            # source and target due to periodicity.
            edges = graph.edges()
            node_edges = defaultdict(lambda: 0)
            for edge in edges:
                source = edge[0]
                target = edge[1]
                node_edges[source] += 1
                if source != target:
                    node_edges[target] += 1
            n_edges = np.array(list(node_edges.values()))
            mean_edges = n_edges.mean()

            if mean_edges >= 2:
                valid_graphs.append(graph)

        # Each subgraph represents a group of atoms that repeat periodically in
        # each cell. Here we calculate a mean position of these atoms in the
        # cell.
        seed_pos = positions[seed_index]

        group_pos = []
        group_num = []
        seed_group_index = None
        for i_graph, graph in enumerate(valid_graphs):
            nodes = graph.nodes(data=True)
            nodes = [node[0] for node in nodes]
            if seed_index in set(nodes):
                seed_group_index = i_graph
            nodes = np.array(nodes)
            graph_pos = positions[nodes]
            group_pos.append(graph_pos)
            group_num.append(numbers[nodes[0]])

        if n_spans == 3:
            proto_cell, offset = self._find_proto_cell_3d(best_spans, group_pos, group_num, seed_group_index, seed_pos)
        elif n_spans == 2:
            proto_cell, offset = self._find_proto_cell_2d(best_spans, group_pos, group_num, seed_group_index, seed_pos)

        return proto_cell, offset, n_spans

    def _find_proto_cell_3d(self, basis, pos, num, seed_index, seed_pos):
        """
        """
        # Each subgraph represents a group of atoms that repeat periodically in
        # each cell. Here we calculate a mean position of these atoms in the
        # cell.
        basis_element_positions = np.zeros((len(num), 3))
        basis_element_num = []
        for i_group, positions in enumerate(pos):

            # Calculate position in the relative basis of the found cell cell
            scaled_pos = systax.geometry.change_basis(positions, basis, seed_pos)
            scaled_pos %= 1

            # Find the copy with minimum manhattan distance from origin
            manhattan_distances = np.sum(scaled_pos, axis=1)
            min_manhattan_index = np.argmin(manhattan_distances)
            min_manhattan_pos = scaled_pos[min_manhattan_index]

            # All the other copies are moved periodically to be near the min.
            # manhattan copy
            distances = scaled_pos - min_manhattan_pos
            displacement = np.rint(distances)
            final_pos = scaled_pos - displacement

            # The average position is calculated
            group_avg = np.mean(final_pos, axis=0)
            basis_element_positions[i_group] = group_avg
            basis_element_num.append(num[i_group])

        basis_element_num = np.array(basis_element_num)

        offset = basis_element_positions[seed_index]
        proto_cell = Atoms(
            scaled_positions=basis_element_positions,
            symbols=basis_element_num,
            cell=basis
        )

        return proto_cell, offset

    def _find_proto_cell_2d(self, basis, pos, num, seed_index, seed_pos):
        """
        """
        # We need to make the third basis vector
        a = basis[0]
        b = basis[1]
        c = np.cross(a, b)
        c_norm = c/np.linalg.norm(c)
        c_norm = c_norm[None, :]
        basis = np.concatenate((basis, c_norm), axis=0)

        basis_element_positions = np.zeros((len(num), 3))
        basis_element_num = []
        for i_group, positions in enumerate(pos):

            # Calculate position in the relative basis of the found cell cell
            scaled_pos = systax.geometry.change_basis(positions, basis, seed_pos)
            scaled_pos_2d = scaled_pos[:, 0:2]
            scaled_pos_2d %= 1

            # Find the copy with minimum manhattan distance from origin
            manhattan_distances = np.sum(scaled_pos_2d, axis=1)
            min_manhattan_index = np.argmin(manhattan_distances)
            min_manhattan_pos = scaled_pos_2d[min_manhattan_index]

            # All the other copies are moved periodically to be near the min.
            # manhattan copy
            distances = scaled_pos_2d - min_manhattan_pos
            displacement = np.rint(distances)
            final_pos_2d = scaled_pos_2d - displacement

            # The average position is calculated
            scaled_pos[:, 0:2] = final_pos_2d
            group_avg = np.mean(scaled_pos, axis=0)
            basis_element_positions[i_group] = group_avg
            basis_element_num.append(num[i_group])

        basis_element_num = np.array(basis_element_num)

        # Grow the cell to fit all atoms
        c_comp = basis_element_positions[:, 2]
        min_index = np.argmin(c_comp, axis=0)
        max_index = np.argmax(c_comp, axis=0)
        pos_min_rel = np.array([0, 0, c_comp[min_index]])
        pos_max_rel = np.array([0, 0, c_comp[max_index]])
        pos_min_cart = systax.geometry.to_cartesian(basis, pos_min_rel)
        pos_max_cart = systax.geometry.to_cartesian(basis, pos_max_rel)
        c_new_cart = pos_max_cart-pos_min_cart
        # print(c_new_cart)

        # We demand a minimum size for the c-vector even if the system seems to
        # be purely 2-dimensional. This is done because the 3D-space cannot be
        # searched properly if one dimension is flat.
        c_size = np.linalg.norm(c_new_cart)
        min_size = 2*self.pos_tol
        if c_size < min_size:
            c_new_cart = min_size*c_norm
        new_basis = np.array(basis)
        new_basis[2, :] = c_new_cart

        new_scaled_pos = basis_element_positions - pos_min_rel
        new_scaled_pos[:, 2] /= np.linalg.norm(c_new_cart)

        # Create translated system
        # group_seed_pos = pos[seed_index]
        proto_cell = Atoms(
            cell=new_basis,
            scaled_positions=new_scaled_pos,
            symbols=basis_element_num
        )
        offset = proto_cell.get_positions()[seed_index]

        return proto_cell, offset

    def _find_space_dim(self, normed_spans):
        """Used to get the dimensionality of the space that is span by a set of
        vectors.
        """
        angle_threshold = np.pi/180*10  # 10 degrees
        angle_thres_cos = np.cos(angle_threshold)
        angle_thres_sin = np.sin(angle_threshold)

        # Get  triplets of spans (combinations)
        n_basis = len(normed_spans)
        if n_basis == 1:
            return 1
        elif n_basis == 2:
            dot = np.abs(np.dot(normed_spans[0], normed_spans[1]))
            if dot >= angle_thres_cos:
                return 1
            else:
                return 2

        span_indices = range(len(normed_spans))
        indices = np.array(list(itertools.combinations(span_indices, 3)))

        # Calculate the orthogonality tensor from the dot products of the
        # normalized spans
        dots = np.inner(normed_spans, normed_spans)
        a_dot_b_abs = np.abs(dots[indices[:, 0], indices[:, 1]])
        b_dot_c_abs = np.abs(dots[indices[:, 1], indices[:, 2]])
        a_dot_c_abs = np.abs(dots[indices[:, 0], indices[:, 2]])
        ortho_scores = a_dot_b_abs + b_dot_c_abs + a_dot_c_abs

        # Get the triplet with minimum ortho score
        min_idx = ortho_scores.argmin()

        a = normed_spans[indices[min_idx][0]]
        b = normed_spans[indices[min_idx][1]]
        c = normed_spans[indices[min_idx][2]]

        n_ab = np.cross(a, b)
        n_ab_norm = np.linalg.norm(n_ab)
        if n_ab_norm != 0:
            n_ab /= n_ab_norm
        n_bc = np.cross(b, c)
        n_bc_norm = np.linalg.norm(n_bc)
        if n_bc_norm != 0:
            n_bc /= n_bc_norm
        n_ac = np.cross(a, c)
        n_ac_norm = np.linalg.norm(n_ac)
        if n_ac_norm != 0:
            n_ac /= n_ac_norm

        alpha = np.abs(np.dot(n_bc, a))
        beta = np.abs(np.dot(n_ab, c))
        gamma = np.abs(np.dot(n_ac, b))
        angles = np.array((alpha, beta, gamma))

        n_pairs = np.sum(angles >= angle_thres_sin)

        if n_pairs == 3:
            return 3
        else:
            a_dot_b_abs_min = a_dot_b_abs[min_idx]
            b_dot_c_abs_min = b_dot_c_abs[min_idx]
            a_dot_c_abs_min = a_dot_c_abs[min_idx]
            angles = np.array((a_dot_b_abs_min, b_dot_c_abs_min, a_dot_c_abs_min))
            n_pairs = np.sum(angles <= angle_thres_cos)
            if n_pairs >= 1:
                return 2
            else:
                return 1

    # def _find_cell_atoms_3d(self, system, seed_index, cell_basis_vectors):
        # """Finds the atoms that are within the cell defined by the seed atom
        # and the basis vectors.

        # Args:
        # Returns:
            # ASE.Atoms: System representing the found cell.

        # """
        # # Find the atoms within the found cell
        # positions = system.get_positions()
        # numbers = system.get_atomic_numbers()
        # seed_pos = positions[seed_index]
        # indices, rel_pos = systax.geometry.get_positions_within_basis(
            # system,
            # cell_basis_vectors,
            # seed_pos,
            # self.pos_tol)
        # cell_pos = positions[indices]
        # cell_numbers = numbers[indices]

        # proto_sys = Atoms(
            # cell=cell_basis_vectors,
            # positions=cell_pos-seed_pos,
            # symbols=cell_numbers
        # )

        # return proto_sys

    # def _find_cell_atoms_2d(self, system, seed_index, cell_basis_vectors):
        # """Used to find a cell for 2D materials. The best basis that is found
        # only contains two basis vector directions that have been deduced from
        # translational symmetry. The third cell vector is determined by this
        # function by looking at which atoms fullfill the periodic repetition in
        # the neighbourhood of the seed atom and in the direction orthogonal to
        # the found two vectors.

        # Args:
            # system(ASE.Atoms): Original system
            # seed_index(int): Index of the seed atom in system
            # cell_basis_vectors(np.ndarray): Two cell basis vectors that span
                # the 2D system

        # Returns:
            # ASE.Atoms: System representing the found cell.
            # np.ndarray: Position of the seed atom in the new cell.
        # """
        # # Find the atoms that are repeated with the cell
        # pos = system.get_positions()
        # num = system.get_atomic_numbers()
        # seed_pos = pos[seed_index]

        # # Create test basis that is used to find atoms that follow the
        # # translation
        # a = cell_basis_vectors[0]
        # b = cell_basis_vectors[1]
        # c = np.cross(a, b)
        # c_norm = c/np.linalg.norm(c)
        # c_test = 2*self.max_cell_size*c_norm
        # test_basis = np.array((a, b, c_test))
        # origin = seed_pos-0.5*c_test

        # # Convert positions to this basis
        # indices, cell_pos_rel = systax.geometry.get_positions_within_basis(
            # system,
            # test_basis,
            # origin,
            # self.pos_tol,
            # [True, True, True]
        # )
        # # view(system)
        # # print(cell_pos_rel)
        # seed_basis_index = np.where(indices == seed_index)[0][0]
        # cell_pos_cart = systax.geometry.to_cartesian(test_basis, cell_pos_rel)
        # seed_basis_pos_cart = np.array(cell_pos_cart[seed_basis_index])

        # # View system
        # # new_num = num[indices]
        # # test_sys = Atoms(
            # # cell=test_basis,
            # # scaled_positions=cell_pos_rel,
            # # symbols=new_num
        # # )
        # # view(test_sys)

        # # TODO: Add a function that checks that periodic copies are not allowed
        # # in the same cell, or that atoms just outside the edge of the cell are
        # # not missed

        # # TODO: For each atom in the cell, test that after subtraction and addition
        # # in the lattice basis directions there is an identical copy

        # # Determine the real cell thickness by getting the maximum and minimum
        # # heights of the cell
        # c_comp = cell_pos_rel[:, 2]
        # max_index = np.argmax(c_comp)
        # min_index = np.argmin(c_comp)
        # pos_min_rel = np.array([0, 0, c_comp[min_index]])
        # pos_max_rel = np.array([0, 0, c_comp[max_index]])
        # pos_min_cart = systax.geometry.to_cartesian(test_basis, pos_min_rel)
        # pos_max_cart = systax.geometry.to_cartesian(test_basis, pos_max_rel)
        # c_new_cart = pos_max_cart-pos_min_cart

        # # We demand a minimum size for the c-vector even if the system seems to
        # # be purely 2-dimensional. This is done because the 3D-space cannot be
        # # searched properly if one dimension is flat.
        # c_size = np.linalg.norm(c_new_cart)
        # min_size = 2*self.pos_tol
        # if c_size < min_size:
            # c_new_cart = min_size*c_norm
        # offset_cart = (test_basis[2, :]-c_new_cart)/2

        # new_basis = np.array(test_basis)
        # new_basis[2, :] = c_new_cart

        # # Create translated system
        # new_num = num[indices]
        # seed_basis_pos_cart -= offset_cart
        # new_sys = Atoms(
            # cell=new_basis,
            # positions=cell_pos_cart - offset_cart,
            # symbols=new_num
        # )
        # # view(new_sys)

        # return new_sys, seed_basis_pos_cart

    def _find_seed_cm(self, system):
        """Finds the index of the seed point closest to the center of mass of
        the system.
        """
        cm = system.get_center_of_mass()
        positions = system.get_positions()
        distances = systax.geometry.get_distance_matrix(cm, positions)
        min_index = np.argmin(distances)

        return min_index

    def _find_periodic_region(self, system, vacuum_dir, is_2d, tesselation_distance, seed_index, unit_cell, seed_position, periodic_indices):
        """Used to find atoms that are generated by a given unit cell and a
        given origin.

        Args:
            system(ASE.Atoms): Original system from which the periodic
                region is searched
            vacuum_dir(sequence of boolean): The vacuum directions in the
                original simulation cell
            is_2d(boolean): Is the system a 2D material with cells in only one plane
            seed_index(int): Index of the atom from which the search is started
            unit_cell(ASE.Atoms): Repeating unit from which the searched region
                is composed of
            seed_position(np.ndarray): Cartesian position of the seed atom with
                respect to the unit cell origin.
            periodic_indices(sequence of int): Indices of the basis vectors
                that are periodic
            flat_indices(sequence of int): Indices of the basis vectors
                that are nearly zero vectors.
        """
        positions = system.get_positions()
        atomic_numbers = system.get_atomic_numbers()
        seed_pos = positions[seed_index]
        seed_number = atomic_numbers[seed_index]

        # Create a map between an atomic number and indices in the system
        number_to_index_map = {}
        number_to_pos_map = {}
        atomic_number_set = set(atomic_numbers)
        for number in atomic_number_set:
            number_indices = np.where(atomic_numbers == number)[0]
            number_to_index_map[number] = number_indices
            number_to_pos_map[number] = positions[number_indices]

        searched_coords = set()
        used_seed_indices = set()
        queue = deque()
        collection = LinkedUnitCollection(system, unit_cell, is_2d, vacuum_dir, tesselation_distance)

        # view(system)

        # Start off the queue
        self._find_region_rec(
            system,
            collection,
            number_to_index_map,
            number_to_pos_map,
            seed_index,
            seed_pos,
            seed_number,
            unit_cell,
            seed_position,
            searched_coords,
            (0, 0, 0),
            used_seed_indices,
            periodic_indices,
            queue)

        # Keep searching while new cells are found
        finished = False
        while not finished:
            try:
                queue_seed_index, queue_seed_pos, queue_index, queue_cell = queue.popleft()
            except IndexError:
                finished = True
            else:
                self._find_region_rec(
                    system,
                    collection,
                    number_to_index_map,
                    number_to_pos_map,
                    queue_seed_index,
                    queue_seed_pos,
                    seed_number,
                    queue_cell,
                    seed_position,
                    searched_coords,
                    queue_index,
                    used_seed_indices,
                    periodic_indices,
                    queue)

        return collection

    def _find_region_rec(
            self,
            system,
            collection,
            number_to_index_map,
            number_to_pos_map,
            seed_index,
            seed_pos,
            seed_atomic_number,
            unit_cell,
            seed_offset,
            searched_coords,
            index,
            used_seed_indices,
            periodic_indices,
            queue):
        """
        Args:
            system(ASE.Atoms): The original system from which the periodic
                region is searched.
            collection(LinkedUnitCollection): Container for LinkedUnits that
                belong to a found region.
            number_to_index_map(dict): Connects atomic number to indices in the
                original system.
            number_to_pos_map(dict): Connects atomic number to positions in the
                original system.
            seed_index(int): Index of the seed atom in the original system.
            seed_pos(np.ndarray): Position of the seed atom in cartesian coordinates.
            seed_atomic_number(int): Atomic number of the seed atom.
            unit_cell(ASE.Atoms): The current guess for the unit cell.
            seed_offset(np.ndrray): Cartesian offset of the seed atom from the unit cell
                origin.
            searched_coords(set): Set of 3D indices that have been searched.
            index(tuple): The 3D coordinate of this unit cell.
            used_seed_indices(set): The indices that have been used as seeds.
            periodic_indices(sequence of int): The indices of the basis vectors
                that are periodic
        """
        # print("===================")
        # print(seed_index)
        # print(index)
        # print(seed_pos)

        # Check if this cell has already been searched
        if index in searched_coords:
            return
        else:
            searched_coords.add(index)

        # Try to get the scaled positions for atoms in this new cell. If the
        # cell is non-invertible, then this cell is not processed.
        try:
            cell_pos = unit_cell.get_scaled_positions()
        except:
            return
        cell_num = unit_cell.get_atomic_numbers()
        old_basis = unit_cell.get_cell()

        # Here we decide the new seed points where the search is extended. The
        # directions depend on the directions that were found to be periodic
        # for the seed atom.
        n_periodic_dim = len(periodic_indices)
        multipliers = np.array(list(itertools.product((-1, 0, 1), repeat=n_periodic_dim)))
        if n_periodic_dim == 2:
            multis = np.zeros((multipliers.shape[0], multipliers.shape[1]+1))
            multis[:, periodic_indices] = multipliers
            multipliers = multis

        new_seed_indices = []
        new_seed_pos = []
        new_seed_multipliers = []
        orig_pos = system.get_positions()
        orig_cell = system.get_cell()

        # If the seed atom was not found for this cell, end the search
        # TODO: get rid of this loop by using numpy
        i_cell = np.array(old_basis)

        if seed_index is not None:
            for it, multiplier in enumerate(multipliers):

                if tuple(multiplier) == (0, 0, 0):
                    continue

                # If the cell in this index has already been handled, continue
                new_index = tuple(multiplier + np.array(index))
                if new_index in searched_coords:
                    continue

                disloc = np.dot(multiplier, old_basis)
                seed_guess = seed_pos + disloc

                matches, _, _, factors = systax.geometry.get_matches(
                    system,
                    seed_guess,
                    [seed_atomic_number],
                    2*self.pos_tol)

                factor = factors[0]
                match = matches[0]

                # Save the position corresponding to a seed atom or a guess for it.
                # If a match was found that is not the original seed, use it's
                # position to update the cell. If the matched index is the same as
                # the original seed, check the factors array to decide whether to
                # use the guess or not.
                if match is not None:
                    if match != seed_index:
                        i_seed_pos = orig_pos[match]
                    else:
                        if (factor == 0).all():
                            i_seed_pos = seed_guess
                        else:
                            i_seed_pos = orig_pos[match]
                else:
                    i_seed_pos = seed_guess

                # Store the indices and positions of new valid seeds
                if (factor == 0).all():
                    # Check if this index has already been used as a seed. The
                    # used_seed_indices is needed so that the same atom cannot
                    # become a seed point multiple times. This can otherwise
                    # become a problem in e.g. random systems, or "looped"
                    # structures.
                    add = True
                    if match is not None:
                        if match in used_seed_indices:
                            add = False
                    if add:
                        new_seed_indices.append(match)
                        new_seed_pos.append(i_seed_pos)
                        new_seed_multipliers.append(new_index)
                        if match is not None:
                            used_seed_indices.add(match)

                # Store the cell basis vector
                if tuple(multiplier) == (1, 0, 0):
                    if match is None:
                        a = disloc
                    else:
                        temp = i_seed_pos + np.dot(factor, orig_cell)
                        a = temp - seed_pos
                    i_cell[0, :] = a
                elif tuple(multiplier) == (0, 1, 0):
                    if match is None:
                        b = disloc
                    else:
                        temp = i_seed_pos + np.dot(factor, orig_cell)
                        b = temp - seed_pos
                    i_cell[1, :] = b
                elif tuple(multiplier) == (0, 0, 1):
                    if match is None:
                        c = disloc
                    else:
                        temp = i_seed_pos + np.dot(factor, orig_cell)
                        c = temp - seed_pos
                    i_cell[2, :] = c

        # Translate the original system to the seed position
        match_system = system.copy()
        match_system.translate(-seed_pos+seed_offset)

        # Find the atoms that match the positions in the original basis
        matches, substitutions, vacancies, _ = systax.geometry.get_matches(
            match_system,
            unit_cell.get_positions(),
            cell_num,
            2*self.pos_tol,
            )

        # Correct the vacancy positions by the seed pos
        for vacancy in vacancies:
            vacancy.position += seed_pos

        # If there are maches or substitutional atoms in the unit, add it to
        # the collection
        new_unit = LinkedUnit(index, seed_index, seed_pos, i_cell, matches, substitutions, vacancies)
        collection[index] = new_unit

        # Save a snapshot of the process
        # from ase.io import write
        # rec = collection.recreate_valid()
        # rec.set_cell(system.get_cell())
        # num = len(collection)
        # str_num = str(num)
        # str_len = len(str_num)
        # num = (3-str_len)*"0" + str_num
        # write('/home/lauri/Desktop/2d/image_{}.png'.format(num), rec, rotation='-90x,45y,45x', show_unit_cell=2)
        # write('/home/lauri/Desktop/2d/image_{}.png'.format(num), rec, rotation='', show_unit_cell=2)
        # write('/home/lauri/Desktop/curved/image_{}.png'.format(num), rec, rotation='-80x', show_unit_cell=2)
        # write('/home/lauri/Desktop/crystal/image_{}.png'.format(num), rec, rotation='20y,20x', show_unit_cell=2)

        # Save the updated cell shape for the new cells in the queue
        new_cell = Atoms(
            cell=i_cell,
            scaled_positions=cell_pos,
            symbols=cell_num
        )
        # view(new_cell)
        cells = len(new_seed_pos)*[new_cell]

        # Add the found neighbours to a queue
        queue.extend(list(zip(new_seed_indices, new_seed_pos, new_seed_multipliers, cells)))
