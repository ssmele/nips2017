import torch
import numpy as np
import time
import shutil
import json
import numpy
import datetime
import os

from torch.utils.data import DataLoader
from sklearn.model_selection import StratifiedShuffleSplit
from sklearn.preprocessing.label import LabelEncoder
from collections import defaultdict


class PersistenceDiagramProviderCollate:
    def __init__(self, provider, wanted_views: [str] = None,
                 label_map: callable = lambda x: x,
                 output_type=torch.FloatTensor,
                 target_type=torch.LongTensor,
                 gpu=False):
        provided_views = provider.view_names

        if wanted_views is None:
            self.wanted_views = provided_views

        else:
            for wv in wanted_views:
                if wv not in provided_views:
                    raise ValueError('{} is not provided by {} which provides {}'.format(wv, provider, provided_views))

            self.wanted_views = wanted_views

        if not callable(label_map):
            raise ValueError('label_map is expected to be callable.')

        self.label_map = label_map

        self.output_type = output_type
        self.target_type = target_type
        self.gpu = gpu

    def __call__(self, sample_target_iter):
        batch_views, targets = defaultdict(list), []

        for dgm_dict, label in sample_target_iter:
            for view_name in self.wanted_views:
                dgm = list(dgm_dict[view_name])
                dgm = self.output_type(dgm)

                batch_views[view_name].append(dgm)

            targets.append(self.label_map(label))

        targets = self.target_type(targets)

        if self.gpu:
            targets = targets.cuda()

        return batch_views, targets


class SubsetRandomSampler:
    def __init__(self, indices):
        self.indices = indices

    def __iter__(self):
        return (self.indices[i] for i in torch.randperm(len(self.indices)))

    def __len__(self):
        return len(self.indices)


def train_test_from_dataset(dataset,
                            test_size=0.2,
                            batch_size=64,
                            gpu=False,
                            wanted_views=None):

    sample_labels = list(dataset.sample_labels)
    label_encoder = LabelEncoder().fit(sample_labels)
    sample_labels = label_encoder.transform(sample_labels)

    label_map = lambda l: int(label_encoder.transform([l])[0])
    collate_fn = PersistenceDiagramProviderCollate(dataset, label_map=label_map, gpu=gpu, wanted_views=wanted_views)

    sp = StratifiedShuffleSplit(n_splits=1, test_size=test_size)
    train_i, test_i = list(sp.split([0]*len(sample_labels), sample_labels))[0]

    data_train = DataLoader(dataset,
                            batch_size=batch_size,
                            collate_fn=collate_fn,
                            shuffle=False,
                            sampler=SubsetRandomSampler(train_i.tolist()))

    data_test = DataLoader(dataset,
                           batch_size=batch_size,
                           collate_fn=collate_fn,
                           shuffle=False,
                           sampler=SubsetRandomSampler(test_i.tolist()))

    return data_train, data_test


class UpperDiagonalThresholdedLogTransform:
    def __init__(self, nu):
        self.b_1 = (torch.Tensor([1, 1]) / np.sqrt(2))
        self.b_2 = (torch.Tensor([-1, 1]) / np.sqrt(2))
        self.nu = nu

    def __call__(self, dgm):
        if dgm.ndimension() == 0:
            return dgm

        x = torch.mul(dgm, self.b_1.repeat(dgm.size(0), 1))
        x = torch.sum(x, 1).squeeze()
        y = torch.mul(dgm, self.b_2.repeat( dgm.size(0), 1))
        y = torch.sum(y, 1).squeeze()
        i = (y <= self.nu)
        y[i] = torch.log(y[i] / self.nu) + self.nu
        ret = torch.stack([x, y], 1)
        return ret


def pers_dgm_center_init(n_elements):
    centers = []
    while len(centers) < n_elements:
        x = np.random.rand(2)
        if x[1] > x[0]:
            centers.append(x.tolist())

    return torch.Tensor(centers)


def run_experiment_n_times(n, experiment, experiment_file_path):
    tmp_dir_path = os.path.join(os.getcwd(), str(time.time()))
    os.mkdir(tmp_dir_path)

    exp_file_name = os.path.basename(experiment_file_path)
    shutil.copy(experiment_file_path, os.path.join(tmp_dir_path, exp_file_name))

    date = datetime.datetime.now()
    date = date.strftime("%Y-%m-%d %H:%M:%S").replace(' ', '_')

    res_pth = os.path.join(tmp_dir_path, 'results__' + date + '.json')

    result = []

    for i in range(n):

        print('==================^================')
        print('Run {}'.format(i))
        res_of_run = experiment()

        # model = res_of_run['model']
        #
        # with open(os.path.join(tmp_dir_path, 'model_run_{}.pickle'.format(i)), 'bw') as f:
        #     pickle.dump(model, f)

        del res_of_run['model']

        result.append(res_of_run)

        with open(res_pth, 'w') as f:
            json.dump(result, f)

    avg_test_acc = numpy.mean([numpy.mean(r['test_accuracies'][-10:]) for r in result])

    new_folder_name = '{}_{:.2f}_acc_on_{}'.format(exp_file_name.split('.py')[0], avg_test_acc, date)
    new_folder_name.replace('.', '_')
    os.rename(tmp_dir_path, os.path.join(os.path.dirname(tmp_dir_path), new_folder_name))