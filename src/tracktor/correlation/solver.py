from random import shuffle
import numpy as np
import os
import time
import fnmatch
from tqdm import tqdm

import torch
import torchvision
from torch.utils.data import DataLoader
from torch.autograd import Variable
from torch.optim.lr_scheduler import LambdaLR
from torchvision.models.detection.transform import resize_boxes

from tracktor.utils import plot_tracks, get_mot_accum, get_overall_results
from tracktor.frcnn_fpn import FRCNN_FPN
from tracktor.tracker import Tracker
from tracktor.reid.resnet import resnet50
from tracktor.correlation.plot_correlation_dataset import plot_boxes_one_pair

import tensorboardX as tb


class Solver(object):
	default_sgd_args = {"lr": 1e-4,
						 "weight_decay": 0.0,
						 "momentum":0}
	default_optim_args = {"lr": 1e-4}

	def __init__(self, output_dir, optim='SGD', optim_args={}, lr_scheduler_lambda=None):

		optim_args_merged = self.default_optim_args.copy()
		optim_args_merged.update(optim_args)
		self.optim_args = optim_args_merged
		if optim == 'SGD':
			self.optim = torch.optim.SGD
		elif optim == 'Adam':
			self.optim = torch.optim.Adam
		else:
			assert False, "[!] No valid optimizer: {}".format(optim)

		self.lr_scheduler_lambda = lr_scheduler_lambda

		self.checkpoints_dir = os.path.join(output_dir, "checkpoints")
		self.images_dir = os.path.join(output_dir, "validation_images")
		self.tb_dir = output_dir
		
		if not os.path.exists(self.checkpoints_dir):
			os.makedirs(self.checkpoints_dir)
		if not os.path.exists(self.images_dir):
			os.makedirs(self.images_dir)
		if not os.path.exists(self.tb_dir):
			os.makedirs(self.tb_dir)

		self.tracker = None
		self._reset_histories()

	def _reset_histories(self):
		"""
		Resets train and val histories for the accuracy and the loss.
		"""
		self._losses = []
		self._val_losses = []

	def initialize_tracktor(self, model):
		print("Initializing tracktor...")
		from sacred import Experiment
		ex = Experiment()
		ex.add_config('experiments/cfgs/tracktor.yaml')
		ex.add_config(ex.configurations[0]._conf['tracktor']['reid_config'])
		tracktor = ex.configurations[0]._conf['tracktor']
		# reid = ex.configurations[1]._conf['reid']

		# object detection
		obj_detect = FRCNN_FPN(num_classes=2, correlation_head=model)
		obj_detect_model = torch.load(tracktor['obj_detect_model'], map_location=lambda storage, loc: storage)
		correlation_weights = model.state_dict()
		for k in correlation_weights:
			obj_detect_model.update({"correlation_head." + k: correlation_weights[k]})
		obj_detect.load_state_dict(obj_detect_model)
		obj_detect.eval()
		obj_detect.cuda()
				
		# # reid
		# reid_network = resnet50(pretrained=False, **reid['cnn'])
		# reid_network.load_state_dict(torch.load(tracktor['reid_weights'],
		# 							map_location=lambda storage, loc: storage))
		# reid_network.eval()
		# reid_network.cuda()

		self.tracker = Tracker(obj_detect, None, tracktor['tracker'])

	def snapshot(self, model, iter):
		filename = model.name + '_iter_{:d}'.format(iter) + '.pth'
		filename = os.path.join(self.checkpoints_dir, filename)
		parameters = model.state_dict()
		for k in model.state_dict():
			if "roi_heads" in k.split("."): del parameters[k]
		torch.save(parameters, filename)
		print('Wrote snapshot to: {:s}'.format(filename))

		# Delete old snapshots (keep minimum 3 latest)
		snapshots_iters = []

		onlyfiles = [f for f in os.listdir(self.checkpoints_dir) if os.path.isfile(os.path.join(self.checkpoints_dir, f))]

		for f in onlyfiles:
			if fnmatch.fnmatch(f, 'ResNet_iters_*.pth'):
				snapshots_iters.append(int(f.split('_')[2][:-4]))

		snapshots_iters.sort()

		for i in range(len(snapshots_iters) - 3):
			filename = model.name + '_iter_{:d}'.format(snapshots_iters[i]) + '.pth'
			filename = os.path.join(self.checkpoints_dir, filename)
			os.remove(filename)

	def train(self, model, train_loader, val_loader=None, num_epochs=10, log_nth=0, model_args={}):
		"""
		Train a given model with the provided data.

		Inputs:
		- model: model object initialized from a torch.nn.Module
		- train_loader: train data in torch.utils.data.DataLoader
		- val_loader: val data in torch.utils.data.DataLoader
		- num_epochs: total number of training epochs
		- log_nth: log training accuracy and loss every nth iteration
		"""

		self.writer = tb.SummaryWriter(self.tb_dir)
		#self.val_writer = tb.SummaryWriter(self.tb_val_dir)

		# filter out frcnn if this is added to the module
		parameters = [param for name, param in model.named_parameters() if 'frcnn' not in name]
		optim = self.optim(parameters, **self.optim_args)

		if self.lr_scheduler_lambda:
			scheduler = LambdaLR(optim, lr_lambda=self.lr_scheduler_lambda)
		else:
			scheduler = None

		self._reset_histories()
		iter_per_epoch = len(train_loader)

		if not self.tracker:
			self.initialize_tracktor(model)
			model.roi_heads = self.tracker.obj_detect.roi_heads

		print('START TRAIN.')
		############################################################################
		# TODO:                                                                    #
		# Write your own personal training method for our solver. In Each epoch    #
		# iter_per_epoch shuffled training batches are processed. The loss for     #
		# each batch is stored in self.train_loss_history. Every log_nth iteration #
		# the loss is logged. After one epoch the training accuracy of the last    #
		# mini batch is logged and stored in self.train_acc_history.               #
		# We validate at the end of each epoch, log the result and store the       #
		# accuracy of the entire validation set in self.val_acc_history.           #
		#
		# Your logging should like something like:                                 #
		#   ...                                                                    #
		#   [Iteration 700/4800] TRAIN loss: 1.452                                 #
		#   [Iteration 800/4800] TRAIN loss: 1.409                                 #
		#   [Iteration 900/4800] TRAIN loss: 1.374                                 #
		#   [Epoch 1/5] TRAIN acc/loss: 0.560/1.374                                #
		#   [Epoch 1/5] VAL   acc/loss: 0.539/1.310                                #
		#   ...                                                                    #
		############################################################################

		for epoch in range(num_epochs):
			# TRAINING
			if scheduler and epoch:
				scheduler.step()
				print("[*] New learning rate(s): {}".format(scheduler.get_lr()))

			now = time.time()

			for i, batch in enumerate(train_loader, 1):
				#inputs, labels = Variable(batch[0]), Variable(batch[1])

				optim.zero_grad()
				loss = model.losses(batch, **model_args)
				loss.backward()
				optim.step()

				self._losses.append(loss.data.cpu().numpy())

				if log_nth and i % log_nth == 0:
					next_now = time.time()
					print('[Iteration %d/%d] %.3f s/it' % (i + epoch * iter_per_epoch,
																  iter_per_epoch * num_epochs, (next_now-now)/log_nth))
					now = next_now

					last_log_nth_losses = self._losses[-log_nth:]
					train_loss = np.mean(last_log_nth_losses)
					print('%s: %.6f' % ("total_loss", train_loss))
					self.writer.add_scalar("train/total_loss", train_loss, i + epoch * iter_per_epoch)
						
	
			# VALIDATION
			if val_loader and log_nth and epoch % 2 == 0:
				print("Validating...")
				model.eval()
				# mot_accums = []
				# for seq in val_loader:
				# 	print(seq)
				# 	self.tracker.reset()
				# 	data_loader = DataLoader(seq, batch_size=1, shuffle=True)
				# 	for i, frame in enumerate(tqdm(data_loader)):
				# 		#if i > len(seq) * 0.05: break
				# 		with torch.no_grad():
				# 			self.tracker.step(frame)
				# 	mot_accums.append(get_mot_accum(self.tracker.get_results(), seq))
				# results = get_overall_results(mot_accums)

				# for k, v in results.items():
				# 	if k not in self._val_losses.keys():
				# 		self._val_losses[k] = []
				# 	self._val_losses[k].append(v[-1])
		
				images_to_plot = ["MOT17-13_000118_000002", "MOT17-13_000502_000021","MOT17-13_000545_000025", "MOT17-13_000722_000028"]

				for batch in val_loader:
					# If image name in images to plot
					if batch[6][0] in images_to_plot:

						patch1 = Variable(batch[0]).cuda()
						patch2 = Variable(batch[1]).cuda()
						prev_boxes = batch[3].cuda()
						boxes_deltas = model.forward(patch1, patch2)
						prev_boxes = resize_boxes(prev_boxes, batch[8][0], batch[7][0])
						pred_box = model.roi_heads.box_coder.decode(boxes_deltas, [prev_boxes]).squeeze(dim=1)
						pred_box = resize_boxes(pred_box, batch[7][0], batch[8][0])

						prev_image, current_image = plot_boxes_one_pair(batch, (epoch+1) * iter_per_epoch, predictions=pred_box.squeeze(), save=True, output_dir=self.images_dir)

						# img_grid = torchvision.utils.make_grid([prev_image, current_image], padding=20)
						# self.writer.add_image(image_to_plot, img_grid, (epoch+1) * iter_per_epoch)
						# self.writer.add_image(image_to_plot + "_prev", prev_image, (epoch+1) * iter_per_epoch)
						# self.writer.add_image(image_to_plot, current_image, (epoch+1) * iter_per_epoch)
					loss = model.losses(batch, "IoU")
					self._val_losses.append(loss.data.cpu().numpy())

				model.train()

				last_log_nth_losses = self._val_losses[-log_nth:]
				val_loss = np.mean(last_log_nth_losses)
				print('%s: %.3f' % ("IoU", val_loss))
				self.writer.add_scalar("val/iou", val_loss, i + epoch * iter_per_epoch)

				#blobs_val = data_layer_val.forward()
				#tracks_val = model.val_predict(blobs_val)
				#im = plot_tracks(blobs_val, tracks_val)

			self.snapshot(model, (epoch+1)*iter_per_epoch)

			self._reset_histories()

		self.writer.close()
		#self.val_writer.close()

		############################################################################
		#                             END OF YOUR CODE                             #
		############################################################################
		print('FINISH.')
