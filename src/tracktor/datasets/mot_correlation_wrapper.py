from torch.utils.data import Dataset
import torch

from .mot_correlation import MOTcorrelation


class MOTcorrelationWrapper(Dataset):
	"""A Wrapper class for MOTcorrelation.

	Wrapper class for combining different sequences into one dataset for the MOTcorrelation
	Dataset.
	"""

	def __init__(self, split, dataloader):

		train_folders = ['MOT17-02', 'MOT17-04', 'MOT17-05', 'MOT17-09', 'MOT17-10',
				         'MOT17-11', 'MOT17-13']

		self._dataloader = MOTcorrelation(None, split=split, **dataloader)

		for seq in train_folders:
			d = MOTcorrelation(seq, split=split, **dataloader)
			for sample in d.data:
				self._dataloader.data.append(sample)

	def __len__(self):
		return len(self._dataloader.data)

	def __getitem__(self, idx):
		return self._dataloader[idx]