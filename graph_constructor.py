"""
Imports
"""
import numpy as np
from itertools import product
from scipy.spatial import distance_matrix
from evaluator_constructor import distance_calculation, verify_feasibility
from joblib import Parallel, delayed
from itertools import chain
import time
from scipy.stats import norm
import copy

number_cores = 12

def find_sensitive_group_instances(data, feat_val, sensitive_group_dict):
    """
    Finds the instances of the sensitive group given as parameter by index
    """
    sensitive_group_idx = sensitive_group_dict[feat_val]
    sensitive_group_instances = data.transformed_false_undesired_test_df.loc[sensitive_group_idx]
    return sensitive_group_instances

def find_train_specific_feature_val(data, feat, feat_value):
    """
    Finds all the training observations belonging to the feature value of interest
    """
    train_target_df = copy.deepcopy(data.train_df)
    train_target_df['target'] = data.train_target
    train_target_feat_val_df = train_target_df[train_target_df[feat] == feat_value]
    target_feat_val = train_target_feat_val_df['target'].values
    del train_target_feat_val_df['target']
    train_feat_val_np = data.transform_data(train_target_feat_val_df).values
    return train_feat_val_np, target_feat_val

def find_train_desired_label(train_np, train_target, train_pred, extra_search, ioi_label):
    """
    Finds the training instances that have the desired label from either ground truth and/or prediction
    """
    if not extra_search:
        train_cf = train_np[(train_target != ioi_label) & (train_pred != ioi_label)]
    else:
        train_cf = train_np[train_target != ioi_label]
    return train_cf

def make_array(i):
    """
    Method that transforms a generator instance into array  
    """
    list_i = list(i)
    new_list = []
    for j in list_i:
        if isinstance(j, list):
            new_list.extend([k for k in j])
        else:
            new_list.extend([j])
    return np.array(new_list)

def estimate_sensitive_group_positive(data, feat, feat_value):
    """
    Extracts length of the sensitive group test
    """
    sensitive_group_df = data.test_df.loc[(data.test_df[feat] == feat_value) & (data.test_target == data.desired_class)]
    return len(sensitive_group_df)

def continuous_feat_values(i, min_val, max_val, data, continuous_bins):
    """
    Method that defines how to discretize the continuous features
    """
    sorted_feat_i = list(np.sort(data.transformed_train_np[:,i][(data.transformed_train_np[:,i] >= min_val) & (data.transformed_train_np[:,i] <= max_val)]))
    value = list(np.unique(sorted_feat_i))
    if len(value) <= continuous_bins:
        if min_val not in value:
            value = [min_val] + value
        if max_val not in value:
            value = value + [max_val]
        return value
    else:
        mean_val, std_val = np.mean(data.transformed_train_np[:,i]), np.std(data.transformed_train_np[:,i])
        percentiles_range = list(np.linspace(0, 1, continuous_bins + 1))
        value = []
        for perc in percentiles_range:
            value.append(norm.ppf(perc, loc=mean_val, scale=std_val))
        value = [val for val in value if val >= min_val and val <= max_val]
        if min_val not in value:
            value = [min_val] + value
        if max_val not in value:
            value = value + [max_val]
    return value

def verify_prediction_feasibility(data, model, instance, values, i):
    """
    Verifies the feasibility of a node with respect to an instance
    """
    instance_feat_val = instance[i]
    if isinstance(instance_feat_val, np.ndarray):
        values_minus_feat_val = np.sum(np.abs(values - instance_feat_val), axis=1)
    else:
        values_minus_feat_val = values - instance_feat_val
    zip_values_difference = list(zip(values, values_minus_feat_val))
    zip_values_difference.sort(key=lambda x: abs(x[1]))
    if isinstance(instance[i], np.ndarray):
        close_cf_values = [list(instance[i])]
    else:
        close_cf_values = [instance[i]]
    v = copy.deepcopy(instance)
    for tup in zip_values_difference:
        value = tup[0]
        v[i] = value
        if verify_feasibility(instance, v, data) and value not in close_cf_values:
            close_cf_values.extend([value])
            if model.model.predict(v.reshape(1, -1)) != data.undesired_class:
                break
    return close_cf_values

def get_feat_possible_values_parallel(data, model, sensitive_feature_instances, points, continuous_bins, instance_idx, k):
    """
    Method that obtains the features possible values
    """
    instance = sensitive_feature_instances[instance_idx]
    filtered_train_cf_k = points[k]
    v = filtered_train_cf_k - instance 
    nonzero_index = list(np.nonzero(v)[0])
    feat_checked = []
    feat_possible_values = []
    for i in range(len(instance)):
        if i not in feat_checked:
            feat_i = data.processed_features[i]
            if feat_i in data.bin_enc_cols:
                if i in nonzero_index:
                    value = [filtered_train_cf_k[i], instance[i]]
                    value = verify_prediction_feasibility(data, model, instance, value, i)
                else:
                    value = [filtered_train_cf_k[i]]
                feat_checked.extend([i])
            elif feat_i in data.cat_enc_cols:
                idx_cat_i = data.idx_cat_cols_dict[feat_i[:-4]]
                nn_cat_idx = list(filtered_train_cf_k[idx_cat_i])
                if any(item in idx_cat_i for item in nonzero_index):
                    ioi_cat_idx = list(instance[idx_cat_i])
                    value = [nn_cat_idx, ioi_cat_idx]
                    value = verify_prediction_feasibility(data, model, instance, value, idx_cat_i)
                else:
                    value = [nn_cat_idx]
                feat_checked.extend(idx_cat_i)
            elif feat_i in data.ordinal:
                if i in nonzero_index:
                    values_i = list(data.processed_feat_dist[feat_i].keys())
                    max_val_i, min_val_i = max(instance[i], filtered_train_cf_k[i]), min(instance[i], filtered_train_cf_k[i])
                    value = [j for j in values_i if j <= max_val_i and j >= min_val_i]
                    value = verify_prediction_feasibility(data, model, instance, value, i)
                else:
                    value = [filtered_train_cf_k[i]]
                feat_checked.extend([i])
            elif feat_i in data.continuous:
                if i in nonzero_index:
                    max_val_i, min_val_i = max(instance[i], filtered_train_cf_k[i]), min(instance[i], filtered_train_cf_k[i])
                    value = continuous_feat_values(i, min_val_i, max_val_i, data, continuous_bins)
                    value = verify_prediction_feasibility(data, model, instance, value, i)
                else:
                    value = [filtered_train_cf_k[i]]
                feat_checked.extend([i])
            feat_possible_values.append(value)
    return instance_idx, k, feat_possible_values

def get_all_feasibility_parallel(data, sensitive_feature_instances, all_nodes, instance_idx, k):
    """
    Parallelization of feasibility calculation
    """
    instance, node = sensitive_feature_instances[instance_idx - 1], all_nodes[k - 1]
    feasibility = verify_feasibility(instance, node, data)
    return instance_idx, k, feasibility

def get_all_costs_weights_parallel(data, feat, sensitive_feature_instances, all_nodes, instance_idx_to_original_idx_dict, sensitive_group_idx_feat_value_dict, instance_idx, k, type):
    """
    Parallelization of the cost calculation
    """
    instance = sensitive_feature_instances[instance_idx - 1]
    node_k = all_nodes[k - 1]
    distance = distance_calculation(instance, node_k, kwargs={'dat':data, 'type':type})
    original_instance_idx = instance_idx_to_original_idx_dict[instance_idx]
    feat_value = sensitive_group_idx_feat_value_dict[original_instance_idx]
    len_positives_sensitive_group = estimate_sensitive_group_positive(data, feat, feat_value)
    distance = distance/(len_positives_sensitive_group)
    distance2 = distance**2
    return instance_idx, k, distance, distance2
    
def get_graph_nodes_parallel(data, model, sensitive_feature_instances, distance_threshold, feat_possible_values, k, instance_idx, ioi_label, type):
    """
    Parallelization of the graph nodes search
    """
    permutations_list = []
    feat_possible_values_k = feat_possible_values[instance_idx, k]
    permutations = product(*feat_possible_values_k)
    instance = sensitive_feature_instances[instance_idx]
    for i in permutations:
        perm_i = make_array(i)
        if verify_feasibility(instance, perm_i, data):
            if model.model.predict(perm_i.reshape(1, -1)) != ioi_label:
                if distance_calculation(instance, perm_i, kwargs={'dat':data, 'type':type}) < distance_threshold:
                    permutations_list.append(perm_i)
    return permutations_list

def filter_nearest_neighbors(unique_closest_feasible_train_tuple_list, percentage_train_cf_per_feat_value):
    """
    Nearest neighbor filtering according to percentage train cf per feat value
    """
    filtered_closest_train_instances_list = []
    if percentage_train_cf_per_feat_value < 1:
        print(f'Filtering to {percentage_train_cf_per_feat_value} of the training CFs found')
        unique_closest_feasible_train_tuple_list.sort(key=lambda x: x[1])
        threshold_position_idx = int(np.ceil(len(unique_closest_feasible_train_tuple_list)*percentage_train_cf_per_feat_value))
        distance_threshold_cf = unique_closest_feasible_train_tuple_list[threshold_position_idx][1]
        for cf_idx in range(threshold_position_idx):
            if unique_closest_feasible_train_tuple_list[cf_idx][1] <= distance_threshold_cf:
                filtered_closest_train_instances_list.append(unique_closest_feasible_train_tuple_list[cf_idx])
    else:
        filtered_closest_train_instances_list = unique_closest_feasible_train_tuple_list
    return filtered_closest_train_instances_list

def get_closest_feasible_train_desired_label(data, type, x, train_instances):
    """
    Gets the feasible training observations for a given instance
    """
    feasible_train_instances_tuple_list = []
    for train_instance in train_instances:
        if verify_feasibility(x, train_instance, data):
            distance_x_train_instance = distance_calculation(x, train_instance, kwargs={'dat':data, 'type':type})
            feasible_train_instances_tuple_list.append((train_instance, distance_x_train_instance))
    if len(feasible_train_instances_tuple_list) == 0:
        closest_feasible_train_tuple = feasible_train_instances_tuple_list
    else:
        feasible_train_instances_tuple_list.sort(key=lambda x: x[1])
        closest_feasible_train_tuple = feasible_train_instances_tuple_list[0]
    return closest_feasible_train_tuple

def get_nearest_neighbor_parallel(data, model, feat, feat_value, extra_search, sensitive_group_dict, type, percentage_train_cf_per_feat_value):
    """
    Nearest neighbor parallelization
    """
    sensitive_group_instances = find_sensitive_group_instances(data, feat_value, sensitive_group_dict).values
    train_feat_val_np, target_feat_val = find_train_specific_feature_val(data, feat, feat_value)
    train_np_feat_val_pred = model.model.predict(train_feat_val_np)
    train_desired_label_np = find_train_desired_label(train_feat_val_np, target_feat_val, train_np_feat_val_pred, extra_search, data.undesired_class)
    closest_feasible_train_tuple_list = []
    counter = 0
    for sensitive_group_instance in sensitive_group_instances:
        counter += 1
        closest_feasible_train_tuple = get_closest_feasible_train_desired_label(data, type, sensitive_group_instance, train_desired_label_np)
        if len(closest_feasible_train_tuple) == 0 or any(np.array_equal(closest_feasible_train_tuple[0], x[0]) for x in closest_feasible_train_tuple_list):
            continue
        else:
            closest_feasible_train_tuple_list.append(closest_feasible_train_tuple)
    all_unique_closest_feasible_train_np = np.array([i[0] for i in closest_feasible_train_tuple_list])
    # neigh = NearestNeighbors(n_neighbors=1, algorithm='ball_tree', metric=distance_calculation, metric_params={'dat':data, 'type':type}, n_jobs=number_cores)
    # neigh.fit(train_desired_label_np)
    # closest_distances, closest_cf_idx = neigh.kneighbors(sensitive_group_instances, return_distance=True)
    # print(f'NearestNeighbors fit for feat {feat}, feat_value {feat_value}.')
    filtered_closest_feasible_train_instances_list = filter_nearest_neighbors(closest_feasible_train_tuple_list, percentage_train_cf_per_feat_value)
    filtered_closest_feasible_train_np = np.array([i[0] for i in filtered_closest_feasible_train_instances_list])
    avg_filtered_closest_feasible_train_instances_distance = np.mean([i[1] for i in filtered_closest_feasible_train_instances_list])
    print(f'Found {len(all_unique_closest_feasible_train_np)} unique close training CF and distance-filtered to {len(filtered_closest_feasible_train_np)} for {feat} and feat_value {feat_value} under percentage {percentage_train_cf_per_feat_value}, with len instances {len(sensitive_group_instances)}')
    return filtered_closest_feasible_train_np, all_unique_closest_feasible_train_np, avg_filtered_closest_feasible_train_instances_distance

class Graph:

    def __init__(self, data, model, feat, feat_values, sensitive_group_dict, type, percentage, continuous_bins) -> None:
        self.percentage_train_cf_per_feat_value = percentage
        self.feat = feat
        self.feat_values = feat_values
        self.continuous_bins = continuous_bins
        self.sensitive_group_dict = sensitive_group_dict
        self.sensitive_feature_instances, self.sensitive_group_idx_feat_value_dict, self.instance_idx_to_original_idx_dict = self.find_sensitive_feat_instances(data, feat_values)
        self.ioi_label = data.undesired_class
        print('-------------------------------------------------------------------------')
        print('-------------Starting Nearest Training Counterfactual Search-------------')
        print('-------------------------------------------------------------------------')
        self.filtered_train_cf, self.train_cf, self.distance_threshold = self.nearest_neighbor_train_cf(data, model, feat_values, type)
        print('-------------------------------------------------------------------------')
        print('----------------Finding Epsilon for Likelihood calculation---------------')
        print('-------------------------------------------------------------------------')
        self.epsilon = self.get_epsilon(data, dist=type)
        print('-------------------------------------------------------------------------')
        print('----------------------------Constructing Graph---------------------------')
        print('-------------------------------------------------------------------------')
        self.feat_possible_values, self.C, self.C2, self.F, self.rho, self.eta = self.construct_graph(data, model, type)

    def find_sensitive_feat_instances(self, data, feat_values):
        """
        Finds the instances of the sensitive feature by index
        """
        sensitive_feature_instances, sensitive_group_idx_feat_value_dict, instance_idx_to_original_idx_dict, counter = [], {}, {}, 1
        for feat_value in feat_values:
            sensitive_group_instances = find_sensitive_group_instances(data, feat_value, self.sensitive_group_dict)
            sensitive_group_instances_idx = sensitive_group_instances.index.to_list()
            sensitive_group_instances = sensitive_group_instances.values
            sensitive_feature_instances.append(sensitive_group_instances)
            for idx in sensitive_group_instances_idx: 
                sensitive_group_idx_feat_value_dict[idx] = feat_value
                instance_idx_to_original_idx_dict[counter] = idx
                counter += 1
        # filtered_sensitive_feature_instances = filter_infeasible_instances
        sensitive_feature_instances = np.concatenate(sensitive_feature_instances, axis=0)
        return sensitive_feature_instances, sensitive_group_idx_feat_value_dict, instance_idx_to_original_idx_dict

    def nearest_neighbor_train_cf(self, data, model, feat_values, type, extra_search=False):
        """
        Efficiently finds the set of training observations belonging to, and predicted as, the counterfactual class and that belong to the same sensitive group as the centroid (this avoids node generation explosion)
        """
        start_time = time.time()
        results_list = Parallel(n_jobs=number_cores, verbose=10, prefer='processes')(delayed(get_nearest_neighbor_parallel)(data,
                                                                              model,
                                                                              self.feat,
                                                                              feat_value,
                                                                              extra_search,
                                                                              self.sensitive_group_dict,
                                                                              type,
                                                                              self.percentage_train_cf_per_feat_value,
                                                                              ) for feat_value in feat_values) 
        filtered_train_cf_array, all_train_cf_array, closest_filtered_distances = zip(*results_list)
        filtered_train_cf_array = np.concatenate(filtered_train_cf_array, axis=0)
        all_train_cf_array = np.concatenate(all_train_cf_array, axis=0)
        if data.name in ['synthetic_athlete','compass','german','oulad','dutch','adult','student','law','credit']:
            distance_threshold = np.max(closest_filtered_distances)
        elif data.name in []:
            distance_threshold = np.mean(closest_filtered_distances)
        elif data.name in []:
            distance_threshold = np.min(closest_filtered_distances)
        end_time = time.time()
        print(f'Found closest training CFs {len(filtered_train_cf_array)} for len instances {len(self.sensitive_feature_instances)}. (Total time: {(end_time - start_time)})')
        return filtered_train_cf_array, all_train_cf_array, distance_threshold

    def construct_graph(self, data, model, type):
        """
        Constructs the graph and the required parameters to run BIGRACE several lagrange values
        """
        feat_possible_values = self.get_feat_possible_values(data, model)
        print(f'Extracted all possible feature value permutations from training CF. Getting Graph Nodes...')
        self.all_nodes = self.get_graph_nodes(data, model, feat_possible_values, type)
        print(f'Obtained all possible nodes in the graph: {len(self.all_nodes)}. Calculating costs...')
        C, C2 = self.get_all_costs_weights(data, type)
        print(f'Obtained all costs in the graph')
        F = self.get_all_feasibility(data)
        print(f'Obtained all feasibility in the graph')
        rho = self.get_all_likelihood(data, dist=type)
        print(f'Obtained all Likelihood parameter')
        eta = self.get_all_effectiveness(F)
        print(f'Obtained all effectiveness parameter')
        return feat_possible_values, C, C2, F, rho, eta

    def get_feat_possible_values(self, data, model, obj=None, points=None):
        """
        Method that obtains the features possible values
        """
        print(f'Starting feature possible value search...')
        start_time = time.time()
        if obj is None:
            sensitive_feature_instances = self.sensitive_feature_instances
        else:
            sensitive_feature_instances = obj
        if points is None:
            points = self.filtered_train_cf
        else:
            points = points
        feat_possible_values_all = {}
        results_list = Parallel(n_jobs=number_cores, verbose=10, prefer='processes')(delayed(get_feat_possible_values_parallel)(data,
                                                                                                                 model,
                                                                                                                 sensitive_feature_instances,
                                                                                                                 points,
                                                                                                                 self.continuous_bins,
                                                                                                                 instance_idx,
                                                                                                                 k
                                                                                                                 ) for k in range(len(points)) for instance_idx in range(len(sensitive_feature_instances))
                                                                                                                )
        for instance_idx, k, feat_possible_values in results_list:
            feat_possible_values_all[instance_idx, k] = feat_possible_values
        end_time = time.time()
        print(f'Total feature possible value time (s): {(end_time - start_time)}')
        return feat_possible_values_all

    def get_graph_nodes(self, data, model, feat_possible_values, type):
        """
        Generator that contains all the nodes located in the space between the training CFs and the normal_ioi (all possible, CF-labeled nodes)
        """
        graph_nodes = Parallel(n_jobs=number_cores, verbose=10, prefer='processes')(delayed(get_graph_nodes_parallel)(data,
                                                                              model,
                                                                              self.sensitive_feature_instances,
                                                                              self.distance_threshold,
                                                                              feat_possible_values,
                                                                              k,
                                                                              instance_idx,
                                                                              self.ioi_label,
                                                                              type
                                                                              ) for k in range(len(self.filtered_train_cf)) for instance_idx in range(len(self.sensitive_feature_instances)) 
                                            )
        graph_nodes_flat_list = list(chain.from_iterable(graph_nodes))
        graph_nodes_array = np.vstack(graph_nodes_flat_list)
        all_nodes = np.concatenate([self.train_cf, graph_nodes_array], axis=0)
        all_nodes_unique = np.unique(all_nodes, axis=0)
        return all_nodes_unique

    def get_all_costs_weights(self, data, type):
        """
        Method that outputs the cost parameters required for optimization
        """
        C, C2 = {}, {}
        print(f'Starting pairwise distances...')
        start_time = time.time()
        results_list = Parallel(n_jobs=number_cores, verbose=10, prefer='processes')(delayed(get_all_costs_weights_parallel)(data,
                                                                                                                             self.feat,
                                                                                                                             self.sensitive_feature_instances,
                                                                                                                             self.all_nodes,
                                                                                                                             self.instance_idx_to_original_idx_dict,
                                                                                                                             self.sensitive_group_idx_feat_value_dict,
                                                                                                                             instance_idx,
                                                                                                                             k,
                                                                                                                             type
                                                                                                                             ) for k in range(1, len(self.all_nodes) + 1) for instance_idx in range(1, len(self.sensitive_feature_instances) + 1)
                                                                                    )
        for instance_idx, k, distance, distance2 in results_list:
            C[instance_idx, k] = distance
            C2[instance_idx, k] = distance2
        end_time = time.time()
        print(f'Total cost calculation time (s): {(end_time - start_time)}')
        return C, C2

    def get_all_feasibility(self, data):
        """
        Outputs the counterfactual feasibility parameter for all graph nodes (including the training CFs) 
        """
        F = {}
        print(f'Starting feasibility...')
        start_time = time.time()
        results_list = Parallel(n_jobs=number_cores, verbose=10, prefer='processes')(delayed(get_all_feasibility_parallel)(data,
                                                                                                                           self.sensitive_feature_instances,
                                                                                                                           self.all_nodes,
                                                                                                                           instance_idx,
                                                                                                                           k
                                                                                                                           ) for k in range(1, len(self.all_nodes) + 1) for instance_idx in range(1, len(self.sensitive_feature_instances) + 1)
                                                                                     )
        for instance_idx, k, feasibility in results_list:
            F[instance_idx, k] = feasibility
        end_time = time.time()
        print(f'Total feasibility calculation time (s): {(end_time - start_time)}')
        return F
    
    def get_all_effectiveness(self, F):
        """
        Outputs the counterfactual effectiveness parameter for all nodes (including the training CFs)
        """
        eta = {}
        print(f'Starting effectiveness...')
        start_time = time.time()
        for k in range(1, len(self.all_nodes) + 1):
            cumulative_eta = 0
            for instance_idx in range(1, len(self.sensitive_feature_instances) + 1):
                original_instance_idx = self.instance_idx_to_original_idx_dict[instance_idx]
                feat_value = self.sensitive_group_idx_feat_value_dict[original_instance_idx]
                feat_value_idx = self.sensitive_group_dict[feat_value]
                len_sensitive_group = len(feat_value_idx)
                cumulative_eta += F[instance_idx, k]/len_sensitive_group
            eta[k] = cumulative_eta
        end_time = time.time()
        print(f'Total effectiveness calculation time (s): {(end_time - start_time)}')
        return eta
    
    def get_epsilon(self, data, dist='euclidean'):
        """
        Calculates the distance 
        """
        distance = distance_matrix(data.transformed_train_np, data.transformed_train_np, p=1)
        upper_tri_distance = distance[np.triu_indices(len(data.transformed_train_np), k = 1)]
        return np.std(upper_tri_distance, ddof=1) 

    def get_all_likelihood(self, data, dist='euclidean'):
        """
        Extracts the likelihood of all the nodes obtained
        """
        rho = {}
        distance = distance_matrix(self.all_nodes, data.transformed_train_np, p=1)
        gaussian_kernel = np.exp(-(distance/self.epsilon)**2)
        sum_gaussian_kernel_col = np.sum(gaussian_kernel, axis=1)
        max_sum_gaussian_kernel_col = np.max(sum_gaussian_kernel_col)
        min_sum_gaussian_kernel_col = np.min(sum_gaussian_kernel_col)
        for i in range(1, len(self.all_nodes) + 1):
            rho[i] = (sum_gaussian_kernel_col[i-1] - min_sum_gaussian_kernel_col)/(max_sum_gaussian_kernel_col - min_sum_gaussian_kernel_col)
        return rho